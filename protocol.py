"""Message framing helpers for JSON over TCP.

JSON alone cannot be streamed reliably over TCP because TCP is a byte stream
without message boundaries. We use a 4-byte length prefix to delimit messages.

This module handles the core JSON protocol for text messages. For efficient
file transfers, see binary_protocol.py which uses raw binary frames.

Hybrid protocol design:
- JSON for: discovery, handshakes, text messages, group control (small, infrequent)
- Binary for: file chunks (large, performance-critical)

Rationale:
- Length-prefixed framing is simple and efficient.
- Big-endian ('>I') ensures cross-platform compatibility (Windows/Linux/Android/iOS).
- JSON chosen for readability and easy extension of new message types.
- Hybrid approach: JSON for clarity where we need it, binary where we need speed.
"""

import json
import struct
from typing import Any, Dict, Optional, Tuple


def encode_message(message: Dict[str, Any]) -> bytes:
    """Encode a JSON message with a 4-byte length prefix.
    
    Format: [4-byte length][JSON bytes]
    Reason: Receiver knows exactly how many bytes to read for one message.
    """
    # Compact JSON to save bandwidth.
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    # Pack length as 4-byte big-endian unsigned integer.
    length = struct.pack(">I", len(data))
    return length + data


def read_exact(sock, num_bytes: int) -> Optional[bytes]:
    """Read an exact number of bytes or return None on disconnect.
    
    Reason: TCP recv() may return less than requested; we loop until complete.
    Empty chunk signals clean disconnect.
    """
    chunks = []
    bytes_read = 0
    while bytes_read < num_bytes:
        # Request remaining bytes.
        chunk = sock.recv(num_bytes - bytes_read)
        if not chunk:
            # Peer closed connection cleanly.
            return None
        chunks.append(chunk)
        bytes_read += len(chunk)
    # Reassemble all chunks into single bytestring.
    return b"".join(chunks)


def read_message(sock) -> Optional[Dict[str, Any]]:
    """Read a single length-prefixed JSON message from a socket.
    
    Reason: Two-phase read (length then payload) ensures we never read
    partial messages or consume data from the next message.
    """
    # Phase 1: Read 4-byte length prefix.
    length_bytes = read_exact(sock, 4)
    if not length_bytes:
        # Connection closed before length arrived.
        return None
    # Unpack big-endian unsigned int.
    length = struct.unpack(">I", length_bytes)[0]
    # Phase 2: Read exact payload bytes.
    data = read_exact(sock, length)
    if data is None:
        # Connection closed mid-message.
        return None
    try:
        # Decode UTF-8 JSON.
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        # Malformed JSON; treat as protocol error.
        return None


def validate_message(message: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate required envelope fields for a decoded message.
    
    Reason: Early validation prevents downstream errors from malformed messages.
    All messages must have type, device_id, timestamp for routing and logging.
    """
    if "type" not in message:
        return False, "missing type"
    if "device_id" not in message:
        return False, "missing device_id"
    if "timestamp" not in message:
        return False, "missing timestamp"
    return True, ""
