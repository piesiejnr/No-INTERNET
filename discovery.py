"""UDP broadcast discovery service for LAN peers.

This module handles automatic peer discovery on the local network using
UDP broadcast. It continuously announces this device's presence and listens
for announcements from others.

Rationale:
- UDP chosen for its broadcast capability and low overhead.
- Broadcast avoids need for multicast (often blocked/requires IGMP).
- Continuous announcements handle dynamic joins/leaves gracefully.
"""

import json
import socket
import threading
import time
from typing import Callable, Dict, Optional

from utils import get_device_id, get_device_name, get_platform, get_timestamp, get_local_ip

# Port 50000 chosen to avoid conflicts with well-known services.
DISCOVERY_PORT = 50000
# Broadcast to all devices on LAN; routers typically block this from leaving subnet.
BROADCAST_ADDR = "255.255.255.255"


class DiscoveryService:
    """Broadcasts discovery requests and listens for responses.
    
    Reason: Separate class isolates discovery from connection logic.
    Two threads (broadcast + listen) avoid blocking each other.
    """
    def __init__(self, tcp_port: int, on_device: Callable[[Dict[str, str]], None]) -> None:
        self.tcp_port = tcp_port
        self.on_device = on_device
        self.running = False
        self.listener_thread: Optional[threading.Thread] = None
        self.broadcast_thread: Optional[threading.Thread] = None
        self.sock: Optional[socket.socket] = None

    def start(self) -> None:
        """Initialize UDP socket and start broadcast/listen threads.
        
        Reason: SO_BROADCAST required to send to 255.255.255.255.
        SO_REUSEADDR allows multiple instances on same machine (testing).
        """
        if self.running:
            return
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Allow address reuse for testing multiple clients on one machine.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Enable broadcast mode for LAN-wide announcements.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Bind to discovery port to receive announcements from others.
        self.sock.bind(("", DISCOVERY_PORT))
        # Spawn listener thread to process incoming announcements.
        self.listener_thread = threading.Thread(target=self._listen, daemon=True)
        self.listener_thread.start()
        # Spawn broadcaster to periodically announce our presence.
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.broadcast_thread.start()

    def stop(self) -> None:
        self.running = False
        if self.sock:
            self.sock.close()

    def _broadcast_loop(self) -> None:
        """Periodically broadcast discovery request to LAN.
        
        Reason: Continuous announcements handle peers joining at any time.
        3 second interval balances discovery speed vs network overhead.
        """
        while self.running and self.sock:
            # Build discovery announcement with our identity and TCP endpoint.
            message = {
                "type": "discovery_request",
                "device_id": get_device_id(),
                "device_name": get_device_name(),
                "platform": get_platform(),
                "ip": get_local_ip(),
                "tcp_port": self.tcp_port,
                "timestamp": get_timestamp(),
            }
            # Compact JSON to minimize packet size.
            data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            try:
                # Broadcast to all devices on LAN.
                self.sock.sendto(data, (BROADCAST_ADDR, DISCOVERY_PORT))
            except OSError:
                # Network error; continue silently to retry next cycle.
                pass
            # Wait before next announcement to avoid flooding the network.
            time.sleep(3)

    def _listen(self) -> None:
        """Listen for discovery packets and respond or notify UI.
        
        Reason: Blocking recvfrom is fine; dedicated thread prevents blocking other logic.
        """
        while self.running and self.sock:
            try:
                # Wait for incoming UDP packet.
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                # Socket closed; exit loop.
                break
            try:
                message = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                # Malformed packet; ignore and continue.
                continue

            msg_type = message.get("type")
            if msg_type == "discovery_request":
                # Another peer is announcing; send unicast response.
                self._send_response(addr[0])
            elif msg_type == "discovery_response":
                # Got a response; ignore if it's from ourselves.
                if message.get("device_id") == get_device_id():
                    continue
                # Notify UI layer of discovered peer.
                self.on_device(message)

    def _send_response(self, ip: str) -> None:
        """Send unicast discovery response to a specific peer.
        
        Reason: Unicast response avoids duplicate broadcasts.
        Peer gets our TCP endpoint for direct connection.
        """
        if not self.sock:
            return
        # Build response with our identity and TCP endpoint.
        message = {
            "type": "discovery_response",
            "device_id": get_device_id(),
            "device_name": get_device_name(),
            "platform": get_platform(),
            "ip": get_local_ip(),
            "tcp_port": self.tcp_port,
            "timestamp": get_timestamp(),
        }
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        try:
            # Send directly to requester; no broadcast.
            self.sock.sendto(data, (ip, DISCOVERY_PORT))
        except OSError:
            # Network error; silently continue.
            pass
