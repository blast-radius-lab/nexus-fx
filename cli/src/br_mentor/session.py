"""Session persistence — save/load conversation history and phase progress."""

import json
from pathlib import Path

SESSION_FILE = Path.home() / ".config" / "br-mentor" / "session.json"

PHASE_ORDER = [
    "containerization",
    "ci",
    "observability",
    "slo",
    "chaos",
    "cd",
]


def load_session() -> tuple[list[dict], str, dict | None]:
    """Load saved conversation history, current phase, and quiz state."""
    if not SESSION_FILE.exists():
        return [], PHASE_ORDER[0], None
    try:
        data = json.loads(SESSION_FILE.read_text())
        if isinstance(data, dict):
            messages = data.get("messages", [])
            phase = data.get("phase", PHASE_ORDER[0])
            quiz_state = data.get("quiz_state")
            return messages, phase, quiz_state
        if isinstance(data, list):
            return data, PHASE_ORDER[0], None
        return [], PHASE_ORDER[0], None
    except (json.JSONDecodeError, KeyError):
        return [], PHASE_ORDER[0], None


def save_session(messages: list[dict], phase: str, quiz_state: dict | None = None) -> None:
    """Persist conversation history, phase, and quiz state to disk."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"messages": messages, "phase": phase}
    if quiz_state:
        data["quiz_state"] = quiz_state
    SESSION_FILE.write_text(json.dumps(data, indent=2))


def advance_phase(current: str) -> str:
    """Return the next phase, or current if already at the end."""
    try:
        idx = PHASE_ORDER.index(current)
        if idx + 1 < len(PHASE_ORDER):
            return PHASE_ORDER[idx + 1]
    except ValueError:
        pass
    return current


def clear_session() -> None:
    """Delete saved session."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
