"""Context gathering - reads local files and git state to send to the mentor."""

import subprocess
from pathlib import Path


def write_file_content(file_path: str, content: str) -> bool:
    """Write content to a file. Creates parent directories if needed. Returns True on success."""
    path = Path(file_path).resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def read_file_content(file_path: str) -> str | None:
    """Read a file and return its contents, or None if it can't be read."""
    path = Path(file_path).resolve()
    if not path.exists():
        return None
    if not path.is_file():
        return None
    # Skip binary files and very large files
    if path.stat().st_size > 100_000:  # 100KB limit
        return f"[File too large: {path.stat().st_size} bytes]"
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "[Binary file - cannot display]"


def get_git_diff() -> str | None:
    """Get the current git diff (staged + unstaged) from the working directory."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_git_status() -> str | None:
    """Get git status summary."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def gather_context(
    file_paths: list[str],
    include_git_diff: bool = False,
) -> str | None:
    """
    Gather context from local files and git state.

    Returns a formatted string with all context, or None if nothing to include.
    """
    sections: list[str] = []

    # Read requested files
    for file_path in file_paths:
        content = read_file_content(file_path)
        if content:
            sections.append(f"--- File: {file_path} ---\n{content}")

    # Git diff if requested
    if include_git_diff:
        diff = get_git_diff()
        if diff:
            sections.append(f"--- Git Diff ---\n{diff}")

        status = get_git_status()
        if status:
            sections.append(f"--- Git Status ---\n{status}")

    if not sections:
        return None

    return "\n\n".join(sections)
