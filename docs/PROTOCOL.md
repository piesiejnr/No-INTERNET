# LynkLAN Protocol

## Transport
- TCP stream with a 4-byte big-endian length prefix per JSON message.
- UDP broadcast used only for discovery.

## Message Envelope

```json
{
  "type": "handshake | message | file_meta | file_chunk | ack",
  "device_id": "uuid",
  "device_name": "Device Name",
  "platform": "android | ios | pc",
  "timestamp": 1700000000,
  "payload": {}
}
```

## Discovery (UDP)

Discovery Request:

```json
{
  "type": "discovery_request",
  "device_id": "uuid",
  "device_name": "Device Name",
  "platform": "pc",
  "ip": "192.168.43.102",
  "tcp_port": 60000,
  "timestamp": 1700000000
}
```

Discovery Response:

```json
{
  "type": "discovery_response",
  "device_id": "uuid",
  "device_name": "Device Name",
  "platform": "android",
  "ip": "192.168.43.34",
  "tcp_port": 60000,
  "timestamp": 1700000000
}
```

## Handshake

```json
{
  "type": "handshake",
  "device_id": "uuid",
  "device_name": "Android 13",
  "platform": "android",
  "timestamp": 1700000000
}
```

## Text Message

```json
{
  "type": "message",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "text": "Hello from PC"
  }
}
```

## File Transfer

File Metadata:

```json
{
  "type": "file_meta",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "file_id": "uuid",
    "filename": "notes.pdf",
    "size": 204800
  }
}
```

File Chunk:

```json
{
  "type": "file_chunk",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "file_id": "uuid",
    "data": "<base64>"
  }
}
```
