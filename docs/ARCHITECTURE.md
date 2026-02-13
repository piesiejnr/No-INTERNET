# LynkLAN Architecture

## High-level Diagram

```mermaid
flowchart LR
    A[Android] <-- UDP discovery --> B[PC Client]
    C[iPhone] <-- UDP discovery --> B
    D[Android] <-- UDP discovery --> B

    A <-- TCP JSON --> B
    C <-- TCP JSON --> B
    D <-- TCP JSON --> B
```

## Components
- Discovery Service: UDP broadcast request/response.
- Connection Manager: TCP server/client, peer lifecycle.
- Protocol Handler: JSON encoding, message validation.
- File Transfer: chunked file streaming.
- Optional Security: HMAC and encryption hooks.
