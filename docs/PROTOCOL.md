# LynkLAN Protocol

## Transport
- TCP stream with a 4-byte big-endian length prefix per JSON message.
- UDP broadcast used only for discovery.

## Message Envelope

```json
{
  "type": "handshake | message | group_invite | group_join | group_join_ack | group_join_reject | group_message | group_master | file_meta | file_chunk | ack",
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

## Group Master Announcement

```json
{
  "type": "group_master",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "group_id": "uuid",
    "name": "team-chat",
    "members": ["uuid1", "uuid2"],
    "master_id": "uuid1",
    "epoch": 1700000000
  }
}
```

## Group Invite

```json
{
  "type": "group_invite",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "group_id": "uuid",
    "name": "team-chat",
    "master_id": "uuid",
    "inviter_id": "uuid"
  }
}
```

## Group Join Request

```json
{
  "type": "group_join",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "group_id": "uuid",
    "name": "team-chat",
    "from_id": "uuid"
  }
}
```

## Group Join Acknowledgement

```json
{
  "type": "group_join_ack",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "group_id": "uuid",
    "name": "team-chat",
    "members": ["uuid1", "uuid2"],
    "master_id": "uuid1",
    "epoch": 1700000000
  }
}
```

## Group Join Reject

```json
{
  "type": "group_join_reject",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "group_id": "uuid",
    "from_id": "uuid"
  }
}
```

## Group Message

```json
{
  "type": "group_message",
  "device_id": "uuid",
  "device_name": "PC",
  "platform": "pc",
  "timestamp": 1700000000,
  "payload": {
    "group_id": "uuid",
    "message_id": "uuid",
    "from_id": "uuid",
    "text": "Hello group"
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
