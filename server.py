"""Thin wrapper for starting the TCP server.

Legacy module from initial design; now superseded by direct use of
ConnectionManager.start_server(). Kept for backward compatibility.

Rationale:
- Initially planned separate client/server modules.
- Unified into ConnectionManager for simplicity.
"""

from connection_manager import ConnectionManager


def start_server(manager: ConnectionManager) -> None:
    """Start TCP listener for inbound peer connections.
    
    Reason: Wrapper allows future server setup hooks (e.g., TLS config).
    """
    manager.start_server()
