"""Local JSONL storage for direct and group chat history.

Messages are stored in append-only JSONL files (one JSON object per line).
Group metadata and membership are stored in a single state.json file.

Rationale:
- JSONL chosen for fast appends and easy tail reading.
- Separate files per conversation prevent one huge file.
- state.json holds groups because they require atomic updates.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

# Local directory for all persisted data.
DATA_DIR = "data"
# Single JSON file for group state (members, master, epoch).
STATE_FILE = os.path.join(DATA_DIR, "state.json")
# Filename prefixes to distinguish direct vs group logs.
DIRECT_PREFIX = "direct_"
GROUP_PREFIX = "group_"


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_state() -> Dict[str, Any]:
    """Load persisted state or return empty default.
    
    Reason: Graceful handling of first run or corrupted file.
    """
    _ensure_dirs()
    if not os.path.exists(STATE_FILE):
        # First run; return empty state.
        return {"groups": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        # Corrupted or unreadable; start fresh.
        return {"groups": {}}
    if "groups" not in data:
        # Ensure groups key exists for consistency.
        data["groups"] = {}
    return data


def _save_state(state: Dict[str, Any]) -> None:
    """Persist state atomically with pretty formatting.
    
    Reason: Indent for readability; sort_keys for reproducible diffs.
    """
    _ensure_dirs()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        # Pretty-print for manual inspection if needed.
        json.dump(state, f, indent=2, sort_keys=True)


def _append_line(path: str, payload: Dict[str, Any]) -> None:
    # JSONL keeps append-only history for cheap writes.
    _ensure_dirs()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _read_lines(path: str, limit: int) -> List[Dict[str, Any]]:
    """Read recent messages from JSONL file.
    
    Reason: Tail-only read avoids loading huge histories into memory.
    Limit=0 means all lines (for exports or full sync).
    """
    if not os.path.exists(path):
        # No history yet; return empty.
        return []
    with open(path, "r", encoding="utf-8") as f:
        # Read all lines into memory; OK for typical chat logs.
        lines = f.readlines()
    # Take last N lines if limit specified.
    tail = lines[-limit:] if limit > 0 else lines
    output = []
    for line in tail:
        try:
            # Parse each line as independent JSON object.
            output.append(json.loads(line.strip()))
        except json.JSONDecodeError:
            # Corrupted line; skip silently.
            continue
    return output


class ChatStore:
    """Persisted storage for groups and message logs.
    
    Reason: Encapsulate all disk I/O in one class for easy testing and portability.
    """

    def __init__(self) -> None:
        """Load existing state or initialize empty."""
        self.state = _load_state()

    def save(self) -> None:
        """Flush in-memory state to disk.
        
        Reason: Explicit save avoids accidental data loss if process crashes.
        """
        _save_state(self.state)

    def create_group(self, name: str, members: List[str], master_id: str) -> str:
        """Create a new group and persist immediately.
        
        Reason: UUID ensures uniqueness across devices.
        Epoch tracks master election to resolve conflicts.
        """
        group_id = str(uuid.uuid4())
        self.state["groups"][group_id] = {
            "name": name,
            "members": sorted(set(members)),  # De-duplicate and sort for consistency.
            "master_id": master_id,
            "epoch": int(time.time()),  # Timestamp for master election logic.
        }
        self.save()  # Persist immediately.
        return group_id

    def upsert_group(
        self, group_id: str, name: str, members: List[str], master_id: str, epoch: int
    ) -> None:
        """Insert or replace group state.
        
        Reason: Used when joining via invite; remote master dictates state.
        """
        self.state["groups"][group_id] = {
            "name": name,
            "members": sorted(set(members)),
            "master_id": master_id,
            "epoch": epoch,
        }
        self.save()

    def update_group(self, group_id: str, update: Dict[str, Any]) -> None:
        """Partial update of group fields.
        
        Reason: Avoids re-specifying all fields when only updating master or members.
        """
        group = self.state["groups"].get(group_id)
        if not group:
            # Group doesn't exist; silently ignore.
            return
        # Merge update into existing group.
        group.update(update)
        if "members" in group:
            # Re-normalize members after update.
            group["members"] = sorted(set(group["members"]))
        self.save()

    def get_groups(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.state.get("groups", {}))

    def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        group = self.state.get("groups", {}).get(group_id)
        return dict(group) if group else None

    def append_direct(self, peer_id: str, message: Dict[str, Any]) -> None:
        path = os.path.join(DATA_DIR, f"{DIRECT_PREFIX}{peer_id}.jsonl")
        _append_line(path, message)

    def append_group(self, group_id: str, message: Dict[str, Any]) -> None:
        path = os.path.join(DATA_DIR, f"{GROUP_PREFIX}{group_id}.jsonl")
        _append_line(path, message)

    def read_direct(self, peer_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        path = os.path.join(DATA_DIR, f"{DIRECT_PREFIX}{peer_id}.jsonl")
        return _read_lines(path, limit)

    def read_group(self, group_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        path = os.path.join(DATA_DIR, f"{GROUP_PREFIX}{group_id}.jsonl")
        return _read_lines(path, limit)
