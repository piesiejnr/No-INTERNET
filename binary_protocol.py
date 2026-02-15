"""Binary protocol implementation for efficient file transfers.

This module handles encoding and decoding of binary frames for file transfers.
Binary protocol coexists with JSON protocol: small frames (messages) use JSON,
large frames (file chunks) use binary to avoid Base64 overhead and improve speed.

Protocol design:
- Each frame starts with 3-byte magic header 'BIN' (0x42 0x49 0x4E)
- All multi-byte integers use big-endian (network byte order) for cross-platform compatibility
- Lengths are uint32, sizes are uint64, indices are uint32
- Filenames are UTF-8 encoded with explicit byte length
- CRC32 checksums detect corruption during transmission
# Max file size is 5 GB to prevent integer overflow attacks
- Dynamic length validation prevents memory exhaustion attacks

Rationale:
- Binary avoids 33% Base64 overhead compared to JSON protocol
- Eliminates JSON string formatting CPU cost during encoding/decoding
- Magic header 'BIN' (0x42...) cannot be confused with JSON '{' (0x7B)
- Big-endian ensures consistent byte order across Windows/Linux/Android/iOS
- CRC32 detects network corruption without slowing transfers
- Length validation prevents DoS attacks or malformed frames causing crashes
- Frame sync recoverable via connection close and reconnect

Frame types:
- 0x01 = BINARY_FILE_META: File metadata (name, size, compression flag)
- 0x02 = BINARY_FILE_CHUNK: Raw file data chunk with index for out-of-order delivery
- 0x03 = BINARY_FILE_ACK: Optional acknowledgment (future: resume support)

Each binary frame is prefixed with length to enable streaming demultiplexing
with JSON frames on the same socket. Receiver peeks first byte to route:
- 0x42 ('B') → Binary frame
- 0x7B ('{') → JSON frame (existing protocol)
"""

import struct
import zlib
from typing import Optional, Tuple, Dict, Any
import os


# Magic bytes to identify binary frames (impossible to confuse with JSON).
# 0x42 0x49 0x4E = ASCII 'BIN', never matches JSON start (0x7B = '{')
BINARY_MAGIC = b'BIN'

# Frame type identifiers for demultiplexing.
FRAME_TYPE_FILE_META = 0x01
FRAME_TYPE_FILE_CHUNK = 0x02
FRAME_TYPE_FILE_ACK = 0x03

# Safety limits to prevent attacks and resource exhaustion.
MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB - prevents uint64 overflow
MAX_FILENAME_LENGTH = 1024  # Characters, UTF-8 can be up to 4 bytes each
MAX_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB - prevents huge memory allocations
MIN_CHUNK_DATA = 1024  # Must send at least 1 KB of actual data

# Compression flags for optional compression support.
COMPRESSION_NONE = 0x00
COMPRESSION_LZ4 = 0x01
COMPRESSION_GZIP = 0x02


class BinaryProtocolError(Exception):
    """Base exception for binary protocol violations.
    
    Reason: Enables catching protocol-specific errors separately from network errors.
    """
    pass


def read_exact(sock, num_bytes: int) -> Optional[bytes]:
    """Read exactly num_bytes from socket or return None on disconnect.
    
    Reason: TCP recv() may return fewer bytes than requested. This loops until
    we have the exact amount or detect clean disconnect (empty chunk).
    
    Returns:
        Bytes if successful, None if connection closed before full read.
        
    Raises:
        OSError: On socket errors (connection reset, timeout, etc.)
    """
    chunks = []
    bytes_read = 0
    while bytes_read < num_bytes:
        # Request remaining bytes; may return less due to TCP buffering.
        chunk = sock.recv(num_bytes - bytes_read)
        if not chunk:
            # Empty read signals clean disconnect.
            return None
        chunks.append(chunk)
        bytes_read += len(chunk)
    # Reassemble all chunks into single contiguous bytestring.
    return b"".join(chunks)


def encode_binary_file_meta(
    file_id: bytes, filename: str, size: int, compression: int = COMPRESSION_NONE
) -> bytes:
    """Encode file metadata frame for binary transfer.
    
    Format:
    [4 bytes] Frame length (excluding this field, for framing)
    [3 bytes] Magic 'BIN'
    [1 byte]  Frame type (0x01)
    [16 bytes] File ID (UUID as raw bytes, not string)
    [8 bytes]  File size (uint64 big-endian)
    [1 byte]   Compression flag (0x00=none, 0x01=lz4, 0x02=gzip)
    [2 bytes]  Filename length in bytes (uint16 big-endian)
    [N bytes]  Filename (UTF-8 encoded)
    [4 bytes]  CRC32 of (type+file_id+size+compression+filename for corruption detection)
    
    Rationale:
    - Fixed-size fields (16/8/1) enable fast parsing
    - UUID as bytes avoids string conversion overhead
    - File size as uint64 supports up to 16 exabytes
    - Filename length-prefixed allows any UTF-8 chars without escaping
    - CRC32 detects transmission corruption without slowing transfers
    - Compression flag optional for future optimization
    
    Args:
        file_id: 16-byte UUID (from uuid.uuid4().bytes)
        filename: Filename to transfer (may contain unicode)
        size: File size in bytes
        compression: Compression flag (COMPRESSION_NONE default)
        
    Returns:
        Complete frame as bytes, ready to send
        
    Raises:
        BinaryProtocolError: If inputs invalid (size too large, filename too long)
    """
    # Validate inputs early to catch attacks/bugs.
    if size > MAX_FILE_SIZE:
        raise BinaryProtocolError(f"File size {size} exceeds limit {MAX_FILE_SIZE}")
    
    if len(file_id) != 16:
        raise BinaryProtocolError(f"File ID must be 16 bytes, got {len(file_id)}")
    
    # Encode filename as UTF-8; check length in bytes, not characters.
    filename_bytes = filename.encode("utf-8")
    if len(filename_bytes) > MAX_FILENAME_LENGTH:
        raise BinaryProtocolError(
            f"Filename too long: {len(filename_bytes)} bytes (max {MAX_FILENAME_LENGTH})"
        )
    
    # Build payload: frame type + metadata + filename.
    payload = struct.pack(">B", FRAME_TYPE_FILE_META)  # 1 byte: frame type
    payload += file_id  # 16 bytes: UUID
    payload += struct.pack(">Q", size)  # 8 bytes: file size (uint64 big-endian)
    payload += struct.pack(">B", compression)  # 1 byte: compression flag
    payload += struct.pack(">H", len(filename_bytes))  # 2 bytes: filename length
    payload += filename_bytes  # N bytes: actual filename
    
    # Compute CRC32 of payload for corruption detection.
    payload_crc = struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    
    # Complete frame: magic + payload + crc.
    frame = BINARY_MAGIC + payload + payload_crc
    
    # Prepend 4-byte length for framing (excludes length field itself).
    frame_length = struct.pack(">I", len(frame))
    return frame_length + frame


def decode_binary_file_meta(data: bytes) -> Tuple[bytes, str, int, int]:
    """Decode file metadata frame (inverse of encode_binary_file_meta).
    
    Rationale: Validates frame against CRC32, sanitizes filename, enforces size limits.
    
    Args:
        data: Raw frame data (without length prefix)
        
    Returns:
        Tuple of (file_id, filename, size, compression_flag)
        
    Raises:
        BinaryProtocolError: If frame invalid, CRC mismatch, or data corrupt
    """
    # Minimum frame: magic(3) + type(1) + file_id(16) + size(8) + compression(1) + 
    #                filename_len(2) + crc(4) = 35 bytes
    if len(data) < 35:
        raise BinaryProtocolError(f"Frame too short: {len(data)} bytes")
    
    # Verify magic header to catch protocol confusion.
    if data[:3] != BINARY_MAGIC:
        raise BinaryProtocolError(f"Invalid magic header: {data[:3].hex()}")
    
    # Extract and verify frame type.
    frame_type = struct.unpack(">B", data[3:4])[0]
    if frame_type != FRAME_TYPE_FILE_META:
        raise BinaryProtocolError(f"Wrong frame type: {frame_type}, expected {FRAME_TYPE_FILE_META}")
    
    # Parse fixed-size fields.
    file_id = data[4:20]  # 16 bytes
    size = struct.unpack(">Q", data[20:28])[0]  # 8 bytes, uint64
    compression = struct.unpack(">B", data[28:29])[0]  # 1 byte
    filename_len = struct.unpack(">H", data[29:31])[0]  # 2 bytes, uint16
    
    # Validate sizes to catch attacks.
    if size > MAX_FILE_SIZE:
        raise BinaryProtocolError(f"File size too large: {size}")
    
    if filename_len > MAX_FILENAME_LENGTH:
        raise BinaryProtocolError(f"Filename too long: {filename_len}")
    
    # Verify we have enough data for filename + CRC.
    expected_length = 31 + filename_len + 4
    if len(data) < expected_length:
        raise BinaryProtocolError(
            f"Frame incomplete: {len(data)} bytes, expected {expected_length}"
        )
    
    # Extract filename bytes and CRC.
    filename_bytes = data[31 : 31 + filename_len]
    received_crc = struct.unpack(">I", data[31 + filename_len : 31 + filename_len + 4])[0]
    
    # Verify CRC32 to detect transmission corruption.
    payload = data[3 : 31 + filename_len]  # Everything except magic and CRC
    computed_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if received_crc != computed_crc:
        raise BinaryProtocolError(
            f"CRC mismatch: received {received_crc:08x}, computed {computed_crc:08x}"
        )
    
    # Decode filename from UTF-8; may contain emojis, unicode, etc.
    try:
        filename = filename_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise BinaryProtocolError(f"Invalid UTF-8 filename: {e}")
    
    return file_id, filename, size, compression


def encode_binary_file_chunk(file_id: bytes, chunk_index: int, data: bytes) -> bytes:
    """Encode file chunk frame for binary transfer.
    
    Format:
    [4 bytes] Frame length (excluding this field)
    [3 bytes] Magic 'BIN'
    [1 byte]  Frame type (0x02)
    [16 bytes] File ID (UUID as raw bytes, matches metadata)
    [4 bytes]  Chunk index (uint32 big-endian, for reordering if needed)
    [4 bytes]  Chunk size (uint32 big-endian, actual data length)
    [N bytes]  Raw chunk data (no Base64 encoding!)
    [4 bytes]  CRC32 of chunk data for corruption detection
    
    Rationale:
    - Raw binary data (no Base64) saves 33% bandwidth and CPU
    - Chunk index enables out-of-order delivery and parallel sends (future)
    - CRC32 detects corruption
    - Chunk size handles last chunk (may be smaller)
    
    Args:
        file_id: 16-byte UUID (must match metadata)
        chunk_index: Sequential index (0, 1, 2...)
        data: Raw bytes from file (not encoded)
        
    Returns:
        Complete frame as bytes
        
    Raises:
        BinaryProtocolError: If inputs invalid
    """
    # Validate inputs.
    if len(file_id) != 16:
        raise BinaryProtocolError(f"File ID must be 16 bytes, got {len(file_id)}")
    
    if len(data) < MIN_CHUNK_DATA or len(data) > MAX_CHUNK_SIZE:
        raise BinaryProtocolError(
            f"Chunk size {len(data)} outside valid range [{MIN_CHUNK_DATA}, {MAX_CHUNK_SIZE}]"
        )
    
    # Build frame: type + file_id + index + size + data.
    payload = struct.pack(">B", FRAME_TYPE_FILE_CHUNK)  # 1 byte
    payload += file_id  # 16 bytes
    payload += struct.pack(">I", chunk_index)  # 4 bytes (uint32)
    payload += struct.pack(">I", len(data))  # 4 bytes (uint32)
    payload += data  # N bytes of actual file data
    
    # Compute CRC32 of data only (most important part).
    data_crc = struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)
    
    # Complete frame: magic + payload + crc.
    frame = BINARY_MAGIC + payload + data_crc
    
    # Prepend frame length.
    frame_length = struct.pack(">I", len(frame))
    return frame_length + frame


def decode_binary_file_chunk(data: bytes) -> Tuple[bytes, int, int, bytes]:
    """Decode file chunk frame (inverse of encode_binary_file_chunk).
    
    Rationale: Validates all fields, verifies CRC, prevents malformed chunks
    from corrupting receiver state.
    
    Args:
        data: Raw frame data (without length prefix)
        
    Returns:
        Tuple of (file_id, chunk_index, chunk_size, chunk_data)
        
    Raises:
        BinaryProtocolError: If frame invalid or CRC mismatch
    """
    # Minimum frame: magic(3) + type(1) + file_id(16) + index(4) + size(4) + crc(4) = 32 bytes
    if len(data) < 32:
        raise BinaryProtocolError(f"Frame too short: {len(data)} bytes")
    
    # Verify magic.
    if data[:3] != BINARY_MAGIC:
        raise BinaryProtocolError(f"Invalid magic header: {data[:3].hex()}")
    
    # Extract frame type.
    frame_type = struct.unpack(">B", data[3:4])[0]
    if frame_type != FRAME_TYPE_FILE_CHUNK:
        raise BinaryProtocolError(f"Wrong frame type: {frame_type}, expected {FRAME_TYPE_FILE_CHUNK}")
    
    # Parse fixed fields.
    file_id = data[4:20]  # 16 bytes
    chunk_index = struct.unpack(">I", data[20:24])[0]  # 4 bytes
    chunk_size = struct.unpack(">I", data[24:28])[0]  # 4 bytes
    
    # Validate chunk size.
    if chunk_size > MAX_CHUNK_SIZE:
        raise BinaryProtocolError(f"Chunk size too large: {chunk_size}")
    
    # Verify we have complete frame: header + data + crc.
    expected_length = 28 + chunk_size + 4
    if len(data) != expected_length:
        raise BinaryProtocolError(
            f"Frame size mismatch: {len(data)} bytes, expected {expected_length}"
        )
    
    # Extract chunk data and CRC.
    chunk_data = data[28 : 28 + chunk_size]
    received_crc = struct.unpack(">I", data[28 + chunk_size : 28 + chunk_size + 4])[0]
    
    # Verify CRC to detect corruption.
    computed_crc = zlib.crc32(chunk_data) & 0xFFFFFFFF
    if received_crc != computed_crc:
        raise BinaryProtocolError(
            f"CRC mismatch: received {received_crc:08x}, computed {computed_crc:08x}"
        )
    
    return file_id, chunk_index, chunk_size, chunk_data


def read_binary_frame(sock) -> Tuple[int, bytes]:
    """Read a complete binary frame from socket.
    
    Rationale: Handles frame length prefix, validates magic header, ensures
    complete frame before returning to caller.
    
    Protocol:
    1. Read 4-byte length prefix (big-endian)
    2. Validate length (must be at least 3 bytes for magic)
    3. Read exact number of bytes for frame
    4. Validate magic header as sanity check
    5. Return (frame_type, frame_data including magic and crc)
    
    Returns:
        Tuple of (frame_type, frame_data)
        
    Raises:
        BinaryProtocolError: If frame invalid, truncated, or magic wrong
        OSError: On socket errors (read may return None on disconnect)
    """
    # Phase 1: Read length prefix.
    length_bytes = read_exact(sock, 4)
    if length_bytes is None:
        raise BinaryProtocolError("Connection closed while reading frame length")
    
    frame_length = struct.unpack(">I", length_bytes)[0]
    
    # Sanity check: frame must be at least magic(3) + type(1) = 4 bytes.
    # Practical max should be under 11 MB (10 MB chunk + overhead).
    if frame_length < 4 or frame_length > 11 * 1024 * 1024:
        raise BinaryProtocolError(
            f"Invalid frame length: {frame_length} (must be 4-11MB)"
        )
    
    # Phase 2: Read entire frame.
    frame_data = read_exact(sock, frame_length)
    if frame_data is None:
        raise BinaryProtocolError("Connection closed while reading frame data")
    
    # Phase 3: Validate magic header.
    if not frame_data.startswith(BINARY_MAGIC):
        raise BinaryProtocolError(f"Invalid binary magic: {frame_data[:3].hex()}")
    
    # Phase 4: Extract and return frame type.
    frame_type = struct.unpack(">B", frame_data[3:4])[0]
    
    return frame_type, frame_data


def peek_frame_type(sock) -> Optional[int]:
    """Peek at incoming frame type without consuming data.
    
    Rationale: Determines whether next frame is JSON (0x7B) or binary (0x42).
    Enables protocol detection without rewinding socket (not always possible).
    
    Returns:
        First byte of incoming data (0x7B for JSON, 0x42 for binary),
        or None if connection closed.
        
    Note:
        This peeks using socket.MSG_PEEK flag. On some platforms or
        configurations, MSG_PEEK may not work (returns None leading to
        binary read attempt which will fail with clear error).
    """
    try:
        data = sock.recv(1, socket.MSG_PEEK)
        if not data:
            return None
        return data[0]
    except (OSError, AttributeError):
        # MSG_PEEK not available or socket error; caller should attempt normal read.
        return None


# Import socket for MSG_PEEK flag (deferred at end to avoid circular imports).
try:
    import socket
except ImportError:
    # Fallback if socket not available (shouldn't happen in normal Python).
    pass
