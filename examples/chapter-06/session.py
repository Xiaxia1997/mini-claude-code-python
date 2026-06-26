"""Chapter 4 reference: save, load, and resume chat sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


def save_session(session_id: str, data: dict[str, Any]) -> Path:
    """Write one session as a readable JSON file and return its path."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_DIR / f"{session_id}.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def load_session(session_id: str) -> dict[str, Any] | None:
    """Load a session by ID; return None when it is missing or corrupted."""
    path = SESSION_DIR / f"{session_id}.json"
    if not path.is_file():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_sessions() -> list[dict[str, Any]]:
    """Return metadata for every readable session file."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    sessions: list[dict[str, Any]] = []

    for path in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            sessions.append(metadata)

    return sessions


def get_latest_session_id() -> str | None:
    """Find the newest session using the ISO timestamp stored in metadata."""
    sessions = list_sessions()
    if not sessions:
        return None

    sessions.sort(key=lambda item: item.get("startTime", ""), reverse=True)
    latest_id = sessions[0].get("id")
    return latest_id if isinstance(latest_id, str) else None
