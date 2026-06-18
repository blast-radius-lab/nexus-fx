"""HTTP client for communicating with the Blast Radius server."""

import json
from collections.abc import Iterator

import httpx


class MentorClient:
    """Client that talks to the Blast Radius server and streams responses."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def chat_stream(
        self,
        messages: list[dict],
        file_context: str | None = None,
        phase: str | None = None,
        quiz_state: dict | None = None,
    ) -> Iterator[str]:
        """
        Send messages to /chat and yield response chunks as they stream back.

        The server returns SSE (Server-Sent Events) with text chunks.
        """
        payload = {
            "messages": messages,
        }
        if file_context:
            payload["file_context"] = file_context
        if phase:
            payload["phase"] = phase
        if quiz_state:
            payload["quiz_state"] = quiz_state

        with httpx.stream(
            "POST",
            f"{self.base_url}/chat",
            json=payload,
            headers=self._headers(),
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
        ) as response:
            if response.status_code == 401:
                raise PermissionError("Authentication failed. Run: br-mentor auth login")
            if response.status_code != 200:
                raise RuntimeError(
                    f"Server returned {response.status_code}: {response.read().decode()}"
                )

            # Parse SSE stream — chunks are JSON-encoded strings
            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk == "[DONE]":
                        break
                    if chunk.startswith("[ERROR]"):
                        yield chunk
                        break
                    try:
                        yield json.loads(chunk)
                    except json.JSONDecodeError:
                        yield chunk

    def apply_file_changes(self, changes: list[dict], console) -> list[str]:
        """
        Apply file changes from the mentor (dev hat mode).

        The server may return file write actions when acting as the dev team
        (e.g., fixing a dependency). Every change requires explicit user approval.
        There is no auto-accept mode.

        Each change dict: {"path": "relative/path", "content": "...", "action": "write|delete"}
        Returns list of paths that were actually applied.
        """
        from pathlib import Path
        from rich.panel import Panel
        from rich.syntax import Syntax

        applied = []
        for change in changes:
            path = Path(change["path"]).resolve()
            action = change.get("action", "write")

            # Show exactly what will change
            if action == "write":
                console.print(Panel(
                    Syntax(change["content"], "python", theme="monokai"),
                    title=f"[yellow]WRITE:[/yellow] {change['path']}",
                    border_style="yellow",
                ))
            elif action == "delete":
                console.print(Panel(
                    f"[red]DELETE:[/red] {change['path']}",
                    border_style="red",
                ))

            # Require explicit approval — no auto-accept, no "allow all"
            approval = console.input("[bold]Apply this change? (y/n):[/bold] ")
            if approval.strip().lower() != "y":
                console.print("[dim]Skipped.[/dim]")
                continue

            if action == "write":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(change["content"], encoding="utf-8")
                applied.append(str(path))
                console.print(f"[green]Applied:[/green] {change['path']}")
            elif action == "delete" and path.exists():
                path.unlink()
                applied.append(str(path))
                console.print(f"[green]Deleted:[/green] {change['path']}")

        return applied

    def get_usage(self) -> dict:
        """Fetch cumulative token usage and cost from the server."""
        resp = httpx.get(
            f"{self.base_url}/usage",
            headers=self._headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def get_progress(self) -> dict | None:
        """Fetch user's progress from server. Returns {phase, items} or None."""
        try:
            resp = httpx.get(
                f"{self.base_url}/progress",
                headers=self._headers(),
                timeout=5.0,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def report_phase(self, phase: str) -> None:
        """Report phase advance to the server for dashboard tracking."""
        try:
            httpx.post(
                f"{self.base_url}/progress/phase",
                json={"phase": phase},
                headers=self._headers(),
                timeout=5.0,
            )
        except Exception:
            pass

    def report_progress(self, phase: str, item_type: str, item_key: str) -> None:
        """Report completion of a curriculum item (task, quiz question, scenario)."""
        try:
            httpx.post(
                f"{self.base_url}/progress/complete",
                json={"phase": phase, "item_type": item_type, "item_key": item_key},
                headers=self._headers(),
                timeout=5.0,
            )
        except Exception:
            pass

    def pull_session(self) -> dict | None:
        """Fetch session from server.

        Returns:
            dict with messages/phase/quiz_state — server has a session
            dict with status="empty" — server reachable, user has no session
            None — server unreachable
        """
        try:
            resp = httpx.get(
                f"{self.base_url}/session",
                headers=self._headers(),
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    def push_session(self, messages: list[dict], phase: str, quiz_state: dict | None = None) -> None:
        """Save session to server."""
        try:
            httpx.put(
                f"{self.base_url}/session",
                json={"messages": messages, "phase": phase, "quiz_state": quiz_state},
                headers=self._headers(),
                timeout=10.0,
            )
        except Exception:
            pass

    def clear_remote_session(self) -> None:
        """Delete session from server."""
        try:
            httpx.delete(
                f"{self.base_url}/session",
                headers=self._headers(),
                timeout=5.0,
            )
        except Exception:
            pass

    def health_check(self) -> bool:
        """Check if the server is reachable."""
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=5.0)
            return resp.status_code == 200
        except httpx.ConnectError:
            return False
