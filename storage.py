import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

DATA_DIR = "data"
STATE_FILE = os.path.join(DATA_DIR, "state.json")
DIRECT_PREFIX = "direct_"
GROUP_PREFIX = "group_"


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_state() -> Dict[str, Any]:
    _ensure_dirs()
    if not os.path.exists(STATE_FILE):
        return {"groups": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"groups": {}}
    if "groups" not in data:
        data["groups"] = {}
    return data


def _save_state(state: Dict[str, Any]) -> None:
    _ensure_dirs()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _append_line(path: str, payload: Dict[str, Any]) -> None:
    _ensure_dirs()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _read_lines(path: str, limit: int) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    tail = lines[-limit:] if limit > 0 else lines
    output = []
    for line in tail:
        try:
            output.append(json.loads(line.strip()))
        except json.JSONDecodeError:
            continue
    return output


class ChatStore:
    def __init__(self) -> None:
        self.state = _load_state()

    def save(self) -> None:
        _save_state(self.state)

    def create_group(self, name: str, members: List[str], master_id: str) -> str:
        group_id = str(uuid.uuid4())
        self.state["groups"][group_id] = {
            "name": name,
            "members": sorted(set(members)),
            "master_id": master_id,
            "epoch": int(time.time()),
        }
        self.save()
        return group_id

    def update_group(self, group_id: str, update: Dict[str, Any]) -> None:
        group = self.state["groups"].get(group_id)
        if not group:
            return
        group.update(update)
        if "members" in group:
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
