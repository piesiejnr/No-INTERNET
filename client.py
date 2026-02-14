"""Thin wrapper for outbound TCP connections.

Legacy module from initial design; now superseded by direct use of
ConnectionManager.connect_to(). Kept for backward compatibility.

Rationale:
- Initially planned separate client/server modules.
- Unified into ConnectionManager for simplicity.
"""

from connection_manager import ConnectionManager


def connect(manager: ConnectionManager, ip: str, port: int) -> None:
    """Initiate TCP connection to peer.
    
    Reason: Wrapper allows future pre-connection hooks (e.g., validation).
    """
    manager.connect_to(ip, port)
