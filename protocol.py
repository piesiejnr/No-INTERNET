import json
import struct
from typing import Any, Dict, Optional, Tuple


def encode_message(message: Dict[str, Any]) -> bytes:
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    length = struct.pack(">I", len(data))
    return length + data


def read_exact(sock, num_bytes: int) -> Optional[bytes]:
    chunks = []
    bytes_read = 0
    while bytes_read < num_bytes:
        chunk = sock.recv(num_bytes - bytes_read)
        if not chunk:
            return None
        chunks.append(chunk)
        bytes_read += len(chunk)
    return b"".join(chunks)


def read_message(sock) -> Optional[Dict[str, Any]]:
    length_bytes = read_exact(sock, 4)
    if not length_bytes:
        return None
    length = struct.unpack(">I", length_bytes)[0]
    data = read_exact(sock, length)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def validate_message(message: Dict[str, Any]) -> Tuple[bool, str]:
    if "type" not in message:
        return False, "missing type"
    if "device_id" not in message:
        return False, "missing device_id"
    if "timestamp" not in message:
        return False, "missing timestamp"
    return True, ""
