"""Device identity and local network utilities.

Provides stable device ID, hostname, platform tag, timestamps, and local IP.
All core identity functions needed by the protocol layer.

Rationale:
- Device ID persisted to file so it survives restarts.
- Platform tag enables cross-platform feature detection.
- Timestamp uses Unix epoch for universal compatibility.
"""

import os
import socket
import uuid
import time

# Persistent file storing this device's UUID.
DEVICE_ID_FILE = "device_id.txt"


def get_device_id() -> str:
    """Load or generate a stable device UUID for this machine.
    
    Reason: Persisting ID ensures peer connections survive app restarts.
    UUID chosen for uniqueness without coordination.
    """
    if os.path.exists(DEVICE_ID_FILE):
        with open(DEVICE_ID_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                # Found existing ID; reuse it.
                return value
    # First run; generate new UUID.
    value = str(uuid.uuid4())
    with open(DEVICE_ID_FILE, "w", encoding="utf-8") as f:
        f.write(value)
    return value


def get_device_name() -> str:
    """Return OS-provided hostname for display.
    
    Reason: Hostname is more user-friendly than UUID for identifying peers.
    """
    return socket.gethostname()


def get_platform() -> str:
    """Return platform tag for protocol messages.
    
    Reason: Enables future platform-specific features.
    """
    return "pc"


def get_timestamp() -> int:
    """Return current Unix timestamp.
    
    Reason: Universal time format; no timezone issues.
    """
    return int(time.time())


def get_local_ip() -> str:
    """Best-effort local IP discovery for LAN communication.
    
    Reason: Connecting to external IP (without actually sending) forces
    OS to select the local interface used for LAN/internet routing.
    Fallback to gethostbyname handles offline scenarios.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to Google DNS; no packets sent, just route selection.
        sock.connect(("8.8.8.8", 80))
        # Get local side of the "connection".
        return sock.getsockname()[0]
    except OSError:
        # No network; fallback to hostname resolution.
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()
