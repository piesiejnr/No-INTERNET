"""Connection management and message routing for TCP peers.

This module is the core networking layer: it owns peer lifecycle (connect, handshake,
disconnect), routes messages by type, implements direct and group messaging, and
manages file transfer sessions.

Supports hybrid protocol:
- JSON for text messages (control, chat, discovery)
- Binary for file transfers (efficient, no Base64 overhead)

Rationale:
- Single class (ConnectionManager) owns all peers to enable atomic state updates.
- Background threads for reads to avoid blocking the main event loop.
- Master-relay group design chosen for simplicity and ordering guarantees.
- Socket write locking prevents concurrent message corruption.
- Dual protocol detection routes incoming data to appropriate handler.
- All socket sends protected by lock to maintain frame boundaries.

Threading model safety:
- One accept thread (server socket, background)
- One read thread per peer (background)
- All socket writes protected by PeerConnection.send_lock (thread-safe)
- All callbacks execute in peer read threads (async to main cli loop)
- Dictionary access to self.peers protected by implicit GIL (Python dict is atomic for simple ops)
"""

import json
import os
import socket
import struct
import threading
import time
from typing import Callable, Dict, Optional, Set, Union

from protocol import encode_message, read_exact
from utils import get_device_id, get_device_name, get_platform, get_timestamp
from file_transfer import FileReceiver, FileSender, sanitize_filename
from storage import ChatStore


class PeerConnection:
    """Wraps a TCP socket and handles inbound reads on a background thread.
    
    Reason: Isolates per-peer I/O from manager logic; simplifies disconnect cleanup.
    Each peer gets a dedicated thread to avoid head-of-line blocking if one peer stalls.
    
    Thread safety:
    - send_lock protects all socket writes (JSON and binary)
    - Ensures frame boundaries not corrupted by concurrent sends
    - Read loop runs in dedicated thread, not shared
    """
    def __init__(
        self,
        sock: socket.socket,
        on_message: Callable[["PeerConnection", Dict], None],
        on_binary: Callable[["PeerConnection", int, bytes], None],
        on_disconnect: Callable[["PeerConnection"], None],
        is_outbound: bool,
    ) -> None:
        self.sock = sock
        self.on_message = on_message
        self.on_binary = on_binary
        self.on_disconnect = on_disconnect
        self.is_outbound = is_outbound
        self.thread: Optional[threading.Thread] = None
        self.device_id: Optional[str] = None
        self.device_name: Optional[str] = None
        self.platform: Optional[str] = None
        self.running = False
        # Lock protects all socket writes from concurrent corruption.
        # Reason: Multiple threads (CLI + file send + group relay) may call send simultaneously.
        self.send_lock = threading.Lock()

    def start(self) -> None:
        """Spawn background thread to read messages from this peer.
        
        Daemon thread means it terminates when main exits; no cleanup needed.
        """
        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def send(self, message: Union[Dict, bytes]) -> None:
        """Encode and send a message or binary data. Thread-safe.
        
        Reason: sendall ensures atomic delivery; partial writes auto-retried.
        Lock prevents concurrent sends from interleaving (protecting frame boundaries).
        
        Args:
            message: Either JSON dict or binary bytes (from binary_protocol)
        """
        # Determine format: JSON dict or binary bytes.
        if isinstance(message, dict):
            # JSON message: encode with length prefix.
            payload = encode_message(message)
        else:
            # Binary data: already framed by binary_protocol (includes length).
            payload = message
        
        # Atomic send: no other thread can send between lock and sendall.
        # Reason: Prevents two messages' bytes interleaving on socket.
        with self.send_lock:
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
        """Continuously read messages (JSON or binary) until disconnect.
        
        Reason: Blocking read is fine; each peer has its own thread.
        All frames are length-prefixed, so we read the length first, then
        route based on payload prefix ('{' for JSON, 'BIN' for binary).
        """
        try:
            while self.running:
                length_bytes = read_exact(self.sock, 4)
                if not length_bytes:
                    break
                length = struct.unpack(">I", length_bytes)[0]
                if length <= 0:
                    print("[unknown frame type] 0x0")
                    break
                payload = read_exact(self.sock, length)
                if payload is None:
                    break
                if not payload:
                    continue

                first_byte = payload[0]
                if first_byte == 0x7B:  # '{' → JSON message
                    try:
                        message = json.loads(payload.decode("utf-8"))
                    except json.JSONDecodeError:
                        print("[protocol error] invalid json payload")
                        break
                    self.on_message(self, message)
                elif payload.startswith(b"BIN"):
                    frame_type = payload[3]
                    self.on_binary(self, frame_type, payload)
                else:
                    print(f"[unknown frame type] {hex(first_byte)}")
                    break
        
        except OSError:
            # Remote closed/reset the connection abruptly.
            # Reason: Prevents crash, allows graceful cleanup.
            pass
        
        # Always notify manager of disconnect for cleanup.
        self.on_disconnect(self)

    def _handle_binary_frame(self, frame_type: int, frame_data: bytes) -> None:
        """Route incoming binary frames to manager."""
        self.on_binary(self, frame_type, frame_data)


class ConnectionManager:
    """Owns peer lifecycle, direct chat, group chat, and file transfer.
    
    Central coordinator for all networking. Single instance per client.
    Reason: Simplifies synchronization; all peer state in one place.
    
    Threading model:
    - One accept thread (server).
    - One read thread per peer.
    - Callbacks execute in peer threads; must be thread-safe.
    """
    def __init__(
        self,
        tcp_port: int,
        on_text: Callable[[str, str], None],
        on_file: Callable[[str, str], None],
        on_group: Callable[[str, str, str], None],
        on_group_invite: Callable[[str, str, str, str], None],
        on_group_notice: Callable[[str], None],
        on_peer_connected: Callable[[str, str], None],
        on_peer_disconnected: Callable[[str], None],
        store: ChatStore,
    ) -> None:
        self.tcp_port = tcp_port
        self.on_text = on_text
        self.on_file = on_file
        self.on_group = on_group
        self.on_group_invite = on_group_invite
        self.on_group_notice = on_group_notice
        self.on_peer_connected = on_peer_connected
        self.on_peer_disconnected = on_peer_disconnected
        self.store = store
        self.server_sock: Optional[socket.socket] = None
        self.server_thread: Optional[threading.Thread] = None
        self.running = False
        self.peers: Dict[str, PeerConnection] = {}
        self.file_receivers: Dict[str, FileReceiver] = {}

    def start_server(self) -> None:
        """Bind TCP server and start accepting inbound connections.
        
        Reason: Must bind early so discovery broadcasts include correct port.
        SO_REUSEADDR allows quick restarts without 'address already in use' errors.
        """
        if self.running:
            return
        self.running = True
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Allow reuse of address for quick restarts during development.
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("", self.tcp_port))  # Bind to all interfaces.
        self.server_sock.listen(5)  # Backlog for concurrent handshakes.
        self.server_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.server_thread.start()

    def stop(self) -> None:
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        for peer in list(self.peers.values()):
            peer.close()

    def connect_to(self, ip: str, port: int) -> bool:
        """Initiate outbound TCP connection to a peer.
        
        Reason: Timeout prevents hanging on unreachable IPs.
        Returns bool so caller can display error without crashing.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 5 second timeout for connect to fail fast on bad IPs.
        sock.settimeout(5)
        try:
            sock.connect((ip, port))
        except OSError:
            # Connection refused, timeout, or network unreachable.
            sock.close()
            return False
        # Clear timeout for normal operation; reads are blocking.
        sock.settimeout(None)
        peer = PeerConnection(sock, self._handle_message, self._handle_binary_frame, self._handle_disconnect, True)
        peer.start()  # Spawn read thread.
        self._send_handshake(peer)  # Initiate protocol handshake.
        return True

    def get_peers(self) -> Dict[str, PeerConnection]:
        return dict(self.peers)

    def send_text(self, peer_id: str, text: str) -> None:
        """Send a direct message to a peer and log it locally.
        
        Reason: Local logging ensures message history survives reconnects.
        Message ID helps de-duplicate if relay logic is added later.
        """
        peer = self.peers.get(peer_id)
        if not peer:
            # Peer not connected; silently drop (could notify UI layer).
            return
        message = {
            "type": "message",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "timestamp": get_timestamp(),
            "payload": {"message_id": self._new_message_id(), "text": text},
        }
        peer.send(message)  # Send over TCP.
        # Store locally; receiver also stores it for their own history.
        self.store.append_direct(peer_id, message)

    def send_file(self, peer_id: str, path: str) -> None:
        peer = self.peers.get(peer_id)
        if not peer:
            return
        sender = FileSender(path)
        file_size = os.path.getsize(path)
        bytes_sent = 0
        start_time = time.time()
        chunk_count = 0
        for message in sender.messages():
            if isinstance(message, dict):
                message.update(
                    {
                        "device_id": get_device_id(),
                        "device_name": get_device_name(),
                        "platform": get_platform(),
                        "timestamp": get_timestamp(),
                    }
                )
                peer.send(message)
            else:
                # Binary frames already encoded; send as-is.
                peer.send(message)
                bytes_sent += len(message)
                chunk_count += 1
                if chunk_count % 10 == 0:
                    elapsed = time.time() - start_time
                    rate = (bytes_sent / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    print(f"\n[file send] {bytes_sent / (1024 * 1024):.2f} MB in {elapsed:.2f}s ({rate:.2f} MB/s)")
        elapsed = time.time() - start_time
        size_mb = file_size / (1024 * 1024)
        speed_mbps = size_mb / elapsed if elapsed > 0 else 0
        print(f"\n[file sent] {path} ({size_mb:.2f} MB in {elapsed:.2f}s, {speed_mbps:.2f} MB/s)")

    def create_group(self, name: str) -> str:
        """Create a new group with this device as master.
        
        Reason: Creator is always master to simplify initial state.
        Broadcast ensures already-connected peers learn about the group.
        """
        self_id = get_device_id()
        # Initialize group with only self; invites sent separately.
        group_id = self.store.create_group(name, [self_id], self_id)
        # Notify connected peers that we're the master (for future joins).
        self._broadcast_group_master(group_id)
        return group_id

    def invite_to_group(self, group_id: str, members: Set[str]) -> None:
        """Send group invites to specified peers.
        
        Master-only operation to prevent unauthorized invites.
        Reason: Explicit invite flow gives users control over group membership.
        """
        group = self.store.get_group(group_id)
        if not group:
            self.on_group_notice("group not found")
            return
        if group.get("master_id") != get_device_id():
            self.on_group_notice("only the master can invite")
            return
        for peer_id in members:
            peer = self.peers.get(peer_id)
            if not peer:
                self.on_group_notice(f"peer not connected: {peer_id}")
                continue
            message = {
                "type": "group_invite",
                "device_id": get_device_id(),
                "device_name": get_device_name(),
                "platform": get_platform(),
                "timestamp": get_timestamp(),
                "payload": {
                    "group_id": group_id,
                    "name": group.get("name"),
                    "master_id": get_device_id(),
                    "inviter_id": get_device_id(),
                },
            }
            peer.send(message)
            self.on_group_notice(f"invite sent to {peer_id}")

    def accept_group_invite(self, group_id: str, master_id: str, name: str) -> None:
        """Accept a pending group invite and request master to add us.
        
        Reason: Two-phase join (accept then master ACK) ensures master controls
        final member list and prevents race conditions.
        """
        # Optimistically create local group state; master will confirm.
        self.store.upsert_group(
            group_id,
            name,
            [get_device_id(), master_id],
            master_id,
            get_timestamp(),
        )
        peer = self.peers.get(master_id)
        if not peer:
            # Can't join if master is offline; user must retry when connected.
            self.on_group_notice("master not connected")
            return
        # Send join request to master.
        message = {
            "type": "group_join",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "timestamp": get_timestamp(),
            "payload": {
                "group_id": group_id,
                "name": name,
                "from_id": get_device_id(),
            },
        }
        peer.send(message)
        self.on_group_notice(f"join request sent to master {master_id}")

    def reject_group_invite(self, group_id: str, master_id: str) -> None:
        # Reject invite and notify the master.
        peer = self.peers.get(master_id)
        if not peer:
            self.on_group_notice("master not connected")
            return
        message = {
            "type": "group_join_reject",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "timestamp": get_timestamp(),
            "payload": {
                "group_id": group_id,
                "from_id": get_device_id(),
            },
        }
        peer.send(message)
        self.on_group_notice(f"reject sent to master {master_id}")

    def send_group_message(self, group_id: str, text: str) -> None:
        """Send a message to a group via the current master.
        
        Reason: Master-relay design ensures total ordering and simplifies logic.
        Fallback election handles offline master gracefully.
        """
        group = self.store.get_group(group_id)
        if not group:
            return
        self_id = get_device_id()
        members = set(group.get("members", []))
        if self_id not in members:
            members.add(self_id)
        # Determine which members are currently connected.
        active_members = members.intersection(self.peers.keys()) | {self_id}
        master_id = group.get("master_id")

        # Elect new master if current one is offline.
        # Reason: Prevents message loss when master disconnects.
        if master_id not in active_members:
            master_id = self._elect_master(active_members)
            self.store.update_group(group_id, {"master_id": master_id, "epoch": get_timestamp()})
            if master_id == self_id:
                # If we became master, broadcast our authority.
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
            # We are the master; store locally and relay to all members.
            self._store_group_message(group_id, message)
            self._relay_group_message(group_id, message, exclude_id=None)
        else:
            # We are a slave; send to master for relay.
            # Reason: Only master relays to prevent message duplication.
            peer = self.peers.get(master_id)
            if peer:
                peer.send(message)

    def _accept_loop(self) -> None:
        while self.running and self.server_sock:
            try:
                client_sock, _ = self.server_sock.accept()
            except OSError:
                break
            peer = PeerConnection(client_sock, self._handle_message, self._handle_binary_frame, self._handle_disconnect, False)
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
        """Broadcast master announcement to group members.
        
        Reason: Ensures peers have consistent view of group state after
        master election or new joins.
        """
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
        """Persist group message and notify UI.
        
        Reason: Local storage enables offline replay and history commands.
        """
        self.store.append_group(group_id, message)
        self.on_group(message.get("device_id", "unknown"), group_id, message.get("payload", {}).get("text", ""))

    def _relay_group_message(self, group_id: str, message: Dict, exclude_id: Optional[str]) -> None:
        """Forward group message to all active members except sender.
        
        Reason: Master is single source of truth; prevents duplicate delivery.
        exclude_id avoids echoing message back to sender.
        """
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
        """Elect new master using deterministic rule.
        
        Reason: Lexicographic sort ensures all peers elect the same master
        without coordination. Prevents split-brain scenarios.
        """
        return sorted(active_members)[0]

    def _new_message_id(self) -> str:
        """Generate unique message ID for idempotency.
        
        Format: <device_id>-<timestamp> ensures uniqueness across peers.
        Reason: Enables future de-duplication if messages are relayed multiple times.
        """
        return f"{get_device_id()}-{get_timestamp()}"

    def _handle_message(self, peer: PeerConnection, message: Dict) -> None:
        """Route inbound messages by type to appropriate handlers.
        
        Reason: Centralized dispatch simplifies adding new message types.
        Called from peer's read thread; must be thread-safe.
        """
        msg_type = message.get("type")
        if msg_type == "handshake":
            # Extract peer identity from handshake.
            peer.device_id = message.get("device_id")
            peer.device_name = message.get("device_name")
            peer.platform = message.get("platform")
            if peer.device_id:
                # Register peer in active connections map.
                self.peers[peer.device_id] = peer
                self.on_peer_connected(peer.device_id, peer.device_name or "unknown")
                # Send our current group master state to new peer.
                self._send_group_state(peer.device_id)
            if not peer.is_outbound:
                # Inbound connection: send our handshake in response.
                self._send_handshake(peer)
            return

        if msg_type == "message":
            # Direct message from peer.
            peer_id = message.get("device_id", "unknown")
            text = message.get("payload", {}).get("text", "")
            self.on_text(peer_id, text)  # Notify UI.
            # Store for history; both sides store for consistency.
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
                self.store.upsert_group(
                    group_id,
                    update.get("name", "group"),
                    update.get("members", []),
                    update.get("master_id", ""),
                    update.get("epoch", get_timestamp()),
                )
            else:
                self.store.update_group(group_id, update)
            return

        if msg_type == "group_invite":
            payload = message.get("payload", {})
            group_id = payload.get("group_id")
            name = payload.get("name", "group")
            master_id = payload.get("master_id")
            inviter_id = payload.get("inviter_id", message.get("device_id", "unknown"))
            if not group_id or not master_id:
                return
            self.on_group_invite(group_id, name, master_id, inviter_id)
            return

        if msg_type == "group_join":
            payload = message.get("payload", {})
            group_id = payload.get("group_id")
            from_id = payload.get("from_id")
            if not group_id or not from_id:
                return
            group = self.store.get_group(group_id)
            if not group or group.get("master_id") != get_device_id():
                return
            members = set(group.get("members", []))
            members.add(from_id)
            self.store.update_group(group_id, {"members": list(members)})
            ack = {
                "type": "group_join_ack",
                "device_id": get_device_id(),
                "device_name": get_device_name(),
                "platform": get_platform(),
                "timestamp": get_timestamp(),
                "payload": {
                    "group_id": group_id,
                    "name": group.get("name"),
                    "members": sorted(members),
                    "master_id": get_device_id(),
                    "epoch": group.get("epoch"),
                },
            }
            peer = self.peers.get(from_id)
            if peer:
                peer.send(ack)
            self._broadcast_group_master(group_id)
            self.on_group_notice(f"member joined {group_id}: {from_id}")
            return

        if msg_type == "group_join_ack":
            payload = message.get("payload", {})
            group_id = payload.get("group_id")
            if not group_id:
                return
            self.store.upsert_group(
                group_id,
                payload.get("name", "group"),
                payload.get("members", []),
                payload.get("master_id", ""),
                payload.get("epoch", get_timestamp()),
            )
            self.on_group_notice(f"joined group {group_id}")
            return

        if msg_type == "group_join_reject":
            payload = message.get("payload", {})
            group_id = payload.get("group_id")
            from_id = payload.get("from_id")
            if not group_id or not from_id:
                return
            self.on_group_notice(f"invite rejected {group_id} by {from_id}")
            return

        if msg_type == "group_message":
            # Group message: store locally, relay if we're master.
            payload = message.get("payload", {})
            group_id = payload.get("group_id")
            if not group_id:
                return
            # Always store locally for history.
            self._store_group_message(group_id, message)
            group = self.store.get_group(group_id)
            if group and group.get("master_id") == get_device_id():
                # We're the master; relay to other members.
                # Reason: Only master relays to prevent message loops.
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
            done = receiver.write_chunk_json(chunk)
            if done:
                path = receiver.close()
                elapsed = getattr(receiver, 'elapsed_time', 0.0)
                size_mb = receiver.size / (1024 * 1024)
                speed_mbps = size_mb / elapsed if elapsed > 0 else 0
                print(f"\n[file received] {path} ({size_mb:.2f} MB in {elapsed:.2f}s, {speed_mbps:.2f} MB/s)")
                self.on_file(peer.device_id or "unknown", path)
                self.file_receivers.pop(file_id, None)
            return

    def _handle_binary_frame(self, peer: PeerConnection, frame_type: int, frame_data: bytes) -> None:
        """Handle binary file transfer frames."""
        try:
            from binary_protocol import (
                BinaryProtocolError,
                FRAME_TYPE_FILE_META,
                FRAME_TYPE_FILE_CHUNK,
                decode_binary_file_meta,
                decode_binary_file_chunk,
            )
        except Exception:
            return

        try:
            if frame_type == FRAME_TYPE_FILE_META:
                file_id, filename, size, _compression = decode_binary_file_meta(frame_data)
                receiver = FileReceiver(file_id, filename, int(size))
                self.file_receivers[file_id] = receiver
                return

            if frame_type == FRAME_TYPE_FILE_CHUNK:
                file_id, chunk_index, _chunk_size, chunk_data = decode_binary_file_chunk(frame_data)
                receiver = self.file_receivers.get(file_id)
                if not receiver:
                    return
                done = receiver.write_chunk_binary(chunk_index, chunk_data)
                if done:
                    path = receiver.close()
                    elapsed = getattr(receiver, 'elapsed_time', 0.0)
                    size_mb = receiver.size / (1024 * 1024)
                    speed_mbps = size_mb / elapsed if elapsed > 0 else 0
                    print(f"\n[file received] {path} ({size_mb:.2f} MB in {elapsed:.2f}s, {speed_mbps:.2f} MB/s)")
                    self.on_file(peer.device_id or "unknown", path)
                    self.file_receivers.pop(file_id, None)
                return
        except BinaryProtocolError as e:
            print(f"[binary protocol error] {e}")

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
