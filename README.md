# LynkLAN PC Client

Python reference client for LAN discovery and peer-to-peer messaging.

## Features
- UDP broadcast discovery
- TCP peer connections
- JSON protocol with length prefix
- Text messaging
- File transfer (chunked, base64)

## Run

```bash
python main.py
```

## Commands
- `peers`
- `discoveries`
- `connect <ip> <port>`
- `connect_discovered <device_id>`
- `msg <peer_id> <text>`
- `sendfile <peer_id> <path>`
- `quit`

## Docs
- Architecture: docs/ARCHITECTURE.md
- Protocol: docs/PROTOCOL.md
