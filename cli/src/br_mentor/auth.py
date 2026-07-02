"""Authentication - token storage and login flow."""

import json
from pathlib import Path

import httpx
from rich.console import Console
from rich.prompt import Prompt

console = Console()

TOKEN_FILE = Path.home() / ".config" / "br-mentor" / "auth.json"


def get_token() -> str | None:
    """Retrieve stored auth token, or None if not authenticated."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return data.get("token")
    except (json.JSONDecodeError, KeyError):
        return None


def get_server_url() -> str | None:
    """Retrieve stored server URL, or None if not authenticated."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return data.get("server_url")
    except (json.JSONDecodeError, KeyError):
        return None


def store_token(token: str, server_url: str) -> None:
    """Persist auth token to disk."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": token,
        "server_url": server_url,
    }))
    TOKEN_FILE.chmod(0o600)


def clear_auth() -> None:
    """Delete stored auth credentials."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def login_flow(server_url: str, token: str | None = None) -> None:
    """
    Authenticate with the server.

    For now this supports two modes:
    1. Direct token: pass --token flag with a pre-shared secret
    2. Interactive: prompt for email/password, exchange for JWT

    In the future this will support device code flow for OAuth.
    """
    if token:
        # Direct token mode - just store it
        store_token(token, server_url)
        return

    # Interactive login
    console.print(f"[dim]Authenticating with {server_url}[/dim]")
    email = Prompt.ask("Email")
    password = Prompt.ask("Password", password=True)

    try:
        response = httpx.post(
            f"{server_url}/auth/login",
            json={"email": email, "password": password},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        store_token(data["token"], server_url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            console.print("[red]Invalid credentials.[/red]")
        else:
            console.print(f"[red]Server error: {e.response.status_code}[/red]")
        raise SystemExit(1)
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to {server_url}[/red]")
        raise SystemExit(1)
