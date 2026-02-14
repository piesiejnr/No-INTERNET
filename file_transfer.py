"""Chunked file transfer over the JSON message channel."""

import base64
import os
import uuid
from typing import Dict, Iterable

CHUNK_SIZE = 64 * 1024
RECEIVED_DIR = "received"


class FileSender:
    """Generates file metadata and chunk messages for sending."""
    def __init__(self, path: str) -> None:
        self.path = path
        self.file_id = str(uuid.uuid4())

    def messages(self) -> Iterable[Dict]:
        # Yield metadata then base64 chunks for streaming.
        size = os.path.getsize(self.path)
        filename = os.path.basename(self.path)
        # First message: metadata.
        yield {
            "type": "file_meta",
            "payload": {
                "file_id": self.file_id,
                "filename": filename,
                "size": size,
            },
        }
        # Subsequent messages: chunks in sequence.
        with open(self.path, "rb") as f:
            while True:
                # Read next chunk from file.
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    # EOF reached; done.
                    break
                # Base64 encode binary data for JSON compatibility.
                yield {
                    "type": "file_chunk",
                    "payload": {
                        "file_id": self.file_id,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    },
                }


class FileReceiver:
    """Reassembles incoming file chunks to disk."""
    def __init__(self, file_id: str, filename: str, size: int) -> None:
        self.file_id = file_id
        self.filename = filename
        self.size = size
        self.bytes_written = 0
        os.makedirs(RECEIVED_DIR, exist_ok=True)
        self.path = os.path.join(RECEIVED_DIR, filename)
        self.file = open(self.path, "wb")

    def write_chunk(self, data: str) -> bool:
        decoded = base64.b64decode(data.encode("ascii"))
        self.file.write(decoded)
        self.bytes_written += len(decoded)
        return self.bytes_written >= self.size

    def close(self) -> str:
        self.file.close()
        return self.path
