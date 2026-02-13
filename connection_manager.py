import socket
import threading
from typing import Callable, Dict, Optional

from protocol import encode_message, read_message
from utils import get_device_id, get_device_name, get_platform, get_timestamp
from file_transfer import FileReceiver, FileSender


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
        on_peer_connected: Callable[[str, str], None],
        on_peer_disconnected: Callable[[str], None],
    ) -> None:
        self.tcp_port = tcp_port
        self.on_text = on_text
        self.on_file = on_file
        self.on_peer_connected = on_peer_connected
        self.on_peer_disconnected = on_peer_disconnected
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
            "payload": {"text": text},
        }
        peer.send(message)

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

    def _handle_message(self, peer: PeerConnection, message: Dict) -> None:
        msg_type = message.get("type")
        if msg_type == "handshake":
            peer.device_id = message.get("device_id")
            peer.device_name = message.get("device_name")
            peer.platform = message.get("platform")
            if peer.device_id:
                self.peers[peer.device_id] = peer
                self.on_peer_connected(peer.device_id, peer.device_name or "unknown")
            if not peer.is_outbound:
                self._send_handshake(peer)
            return

        if msg_type == "message":
            peer_id = message.get("device_id", "unknown")
            text = message.get("payload", {}).get("text", "")
            self.on_text(peer_id, text)
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

    def _handle_disconnect(self, peer: PeerConnection) -> None:
        if peer.device_id and peer.device_id in self.peers:
            self.peers.pop(peer.device_id, None)
            self.on_peer_disconnected(peer.device_id)
        peer.close()
