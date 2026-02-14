import socket
import threading
from typing import Callable, Dict, Optional, Set

from protocol import encode_message, read_message
from utils import get_device_id, get_device_name, get_platform, get_timestamp
from file_transfer import FileReceiver, FileSender
from storage import ChatStore


class PeerConnection:
    def __init__(
        self,
        sock: socket.socket,
        on_message: Callable[["PeerConnection", Dict], None],
        on_disconnect: Callable[["PeerConnection"], None],
        is_outbound: bool,
    ) -> None:
        self.sock = sock
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.is_outbound = is_outbound
        self.thread: Optional[threading.Thread] = None
        self.device_id: Optional[str] = None
        self.device_name: Optional[str] = None
        self.platform: Optional[str] = None
        self.running = False

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def send(self, message: Dict) -> None:
        payload = encode_message(message)
        self.sock.sendall(payload)

    def close(self) -> None:
        self.running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _read_loop(self) -> None:
        try:
            while self.running:
                message = read_message(self.sock)
                if message is None:
                    break
                self.on_message(self, message)
        except OSError:
            # Remote closed/reset the connection.
            pass
        self.on_disconnect(self)


class ConnectionManager:
    def __init__(
        self,
        tcp_port: int,
        on_text: Callable[[str, str], None],
        on_file: Callable[[str, str], None],
        on_group: Callable[[str, str, str], None],
        on_peer_connected: Callable[[str, str], None],
        on_peer_disconnected: Callable[[str], None],
        store: ChatStore,
    ) -> None:
        self.tcp_port = tcp_port
        self.on_text = on_text
        self.on_file = on_file
        self.on_group = on_group
        self.on_peer_connected = on_peer_connected
        self.on_peer_disconnected = on_peer_disconnected
        self.store = store
        self.server_sock: Optional[socket.socket] = None
        self.server_thread: Optional[threading.Thread] = None
        self.running = False
        self.peers: Dict[str, PeerConnection] = {}
        self.file_receivers: Dict[str, FileReceiver] = {}

    def start_server(self) -> None:
        if self.running:
            return
        self.running = True
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("", self.tcp_port))
        self.server_sock.listen(5)
        self.server_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.server_thread.start()

    def stop(self) -> None:
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        for peer in list(self.peers.values()):
            peer.close()

    def connect_to(self, ip: str, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((ip, port))
        except OSError:
            sock.close()
            return False
        sock.settimeout(None)
        peer = PeerConnection(sock, self._handle_message, self._handle_disconnect, True)
        peer.start()
        self._send_handshake(peer)
        return True

    def get_peers(self) -> Dict[str, PeerConnection]:
        return dict(self.peers)

    def send_text(self, peer_id: str, text: str) -> None:
        peer = self.peers.get(peer_id)
        if not peer:
            return
        message = {
            "type": "message",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "timestamp": get_timestamp(),
            "payload": {"message_id": self._new_message_id(), "text": text},
        }
        peer.send(message)
        self.store.append_direct(peer_id, message)

    def send_file(self, peer_id: str, path: str) -> None:
        peer = self.peers.get(peer_id)
        if not peer:
            return
        sender = FileSender(path)
        for message in sender.messages():
            message.update(
                {
                    "device_id": get_device_id(),
                    "device_name": get_device_name(),
                    "platform": get_platform(),
                    "timestamp": get_timestamp(),
                }
            )
            peer.send(message)

    def create_group(self, name: str, members: Set[str]) -> str:
        self_id = get_device_id()
        members.add(self_id)
        group_id = self.store.create_group(name, list(members), self_id)
        self._broadcast_group_master(group_id)
        return group_id

    def send_group_message(self, group_id: str, text: str) -> None:
        group = self.store.get_group(group_id)
        if not group:
            return
        self_id = get_device_id()
        members = set(group.get("members", []))
        if self_id not in members:
            members.add(self_id)
        active_members = members.intersection(self.peers.keys()) | {self_id}
        master_id = group.get("master_id")

        if master_id not in active_members:
            master_id = self._elect_master(active_members)
            self.store.update_group(group_id, {"master_id": master_id, "epoch": get_timestamp()})
            if master_id == self_id:
                self._broadcast_group_master(group_id)

        message = {
            "type": "group_message",
            "device_id": self_id,
            "device_name": get_device_name(),
            "platform": get_platform(),
            "timestamp": get_timestamp(),
            "payload": {
                "group_id": group_id,
                "message_id": self._new_message_id(),
                "text": text,
                "from_id": self_id,
            },
        }

        if master_id == self_id:
            self._store_group_message(group_id, message)
            self._relay_group_message(group_id, message, exclude_id=None)
        else:
            peer = self.peers.get(master_id)
            if peer:
                peer.send(message)

    def _accept_loop(self) -> None:
        while self.running and self.server_sock:
            try:
                client_sock, _ = self.server_sock.accept()
            except OSError:
                break
            peer = PeerConnection(client_sock, self._handle_message, self._handle_disconnect, False)
            peer.start()

    def _send_handshake(self, peer: PeerConnection) -> None:
        message = {
            "type": "handshake",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "timestamp": get_timestamp(),
        }
        peer.send(message)

    def _broadcast_group_master(self, group_id: str) -> None:
        group = self.store.get_group(group_id)
        if not group:
            return
        master_id = group.get("master_id")
        if master_id != get_device_id():
            return
        message = {
            "type": "group_master",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "timestamp": get_timestamp(),
            "payload": {
                "group_id": group_id,
                "name": group.get("name"),
                "members": group.get("members", []),
                "master_id": master_id,
                "epoch": group.get("epoch"),
            },
        }
        for peer_id, peer in self.peers.items():
            if peer_id in set(group.get("members", [])):
                peer.send(message)

    def _store_group_message(self, group_id: str, message: Dict) -> None:
        self.store.append_group(group_id, message)
        self.on_group(message.get("device_id", "unknown"), group_id, message.get("payload", {}).get("text", ""))

    def _relay_group_message(self, group_id: str, message: Dict, exclude_id: Optional[str]) -> None:
        group = self.store.get_group(group_id)
        if not group:
            return
        members = set(group.get("members", []))
        for peer_id, peer in self.peers.items():
            if peer_id not in members:
                continue
            if exclude_id and peer_id == exclude_id:
                continue
            peer.send(message)

    def _elect_master(self, active_members: Set[str]) -> str:
        return sorted(active_members)[0]

    def _new_message_id(self) -> str:
        return f"{get_device_id()}-{get_timestamp()}"

    def _handle_message(self, peer: PeerConnection, message: Dict) -> None:
        msg_type = message.get("type")
        if msg_type == "handshake":
            peer.device_id = message.get("device_id")
            peer.device_name = message.get("device_name")
            peer.platform = message.get("platform")
            if peer.device_id:
                self.peers[peer.device_id] = peer
                self.on_peer_connected(peer.device_id, peer.device_name or "unknown")
                self._send_group_state(peer.device_id)
            if not peer.is_outbound:
                self._send_handshake(peer)
            return

        if msg_type == "message":
            peer_id = message.get("device_id", "unknown")
            text = message.get("payload", {}).get("text", "")
            self.on_text(peer_id, text)
            self.store.append_direct(peer_id, message)
            return

        if msg_type == "group_master":
            payload = message.get("payload", {})
            group_id = payload.get("group_id")
            if not group_id:
                return
            update = {
                "name": payload.get("name", "group"),
                "members": payload.get("members", []),
                "master_id": payload.get("master_id"),
                "epoch": payload.get("epoch", get_timestamp()),
            }
            if self.store.get_group(group_id) is None:
                self.store.state["groups"][group_id] = update
                self.store.save()
            else:
                self.store.update_group(group_id, update)
            return

        if msg_type == "group_message":
            payload = message.get("payload", {})
            group_id = payload.get("group_id")
            if not group_id:
                return
            self._store_group_message(group_id, message)
            group = self.store.get_group(group_id)
            if group and group.get("master_id") == get_device_id():
                self._relay_group_message(group_id, message, exclude_id=message.get("device_id"))
            return

        if msg_type == "file_meta":
            file_id = message.get("payload", {}).get("file_id")
            filename = message.get("payload", {}).get("filename")
            size = message.get("payload", {}).get("size")
            if file_id and filename and size is not None:
                receiver = FileReceiver(file_id, filename, int(size))
                self.file_receivers[file_id] = receiver
            return

        if msg_type == "file_chunk":
            file_id = message.get("payload", {}).get("file_id")
            chunk = message.get("payload", {}).get("data")
            if not file_id or not chunk:
                return
            receiver = self.file_receivers.get(file_id)
            if not receiver:
                return
            done = receiver.write_chunk(chunk)
            if done:
                path = receiver.close()
                self.on_file(peer.device_id or "unknown", path)
                self.file_receivers.pop(file_id, None)
            return

    def _send_group_state(self, peer_id: str) -> None:
        peer = self.peers.get(peer_id)
        if not peer:
            return
        groups = self.store.get_groups()
        for group_id, group in groups.items():
            members = set(group.get("members", []))
            if peer_id not in members:
                continue
            if group.get("master_id") != get_device_id():
                continue
            message = {
                "type": "group_master",
                "device_id": get_device_id(),
                "device_name": get_device_name(),
                "platform": get_platform(),
                "timestamp": get_timestamp(),
                "payload": {
                    "group_id": group_id,
                    "name": group.get("name"),
                    "members": group.get("members", []),
                    "master_id": group.get("master_id"),
                    "epoch": group.get("epoch"),
                },
            }
            peer.send(message)

    def _handle_disconnect(self, peer: PeerConnection) -> None:
        if peer.device_id and peer.device_id in self.peers:
            self.peers.pop(peer.device_id, None)
            self.on_peer_disconnected(peer.device_id)
        peer.close()
