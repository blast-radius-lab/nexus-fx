"""Blast Radius CLI - main entrypoint."""

import fcntl
import os
import re
import subprocess
import sys
import termios
import time
from collections.abc import Iterator

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live

from br_mentor.auth import clear_auth, get_server_url, get_token, login_flow
from br_mentor.client import MentorClient
from br_mentor.context import gather_context, read_file_content, write_file_content, get_git_diff, get_git_status
from br_mentor.session import advance_phase, clear_session, load_session, save_session

app = typer.Typer(
    name="br-mentor",
    help="AI-mentored SRE learning platform CLI",
    no_args_is_help=True,
)
auth_app = typer.Typer(help="Authentication commands")
app.add_typer(auth_app, name="auth")

console = Console()

DEFAULT_SERVER_URL = "https://blastradiuslab.com"

WELCOME_MESSAGE = """\
## Welcome to Blast Radius

You're working on **Nexus FX** — a simulated FX trading platform with three \
FastAPI services and a PostgreSQL database:

| Service | Port | Role |
|---------|------|------|
| API Gateway | 8000 | Auth, REST API, routes requests |
| Engine | 8002 | Order matching, DB persistence |
| Price Service | 8001 | Mock price feed, LP execution |
| PostgreSQL | 5432 | User accounts, orders, LP fills |

The base release ships bare Python services — no Dockerfiles, no CI, no \
observability, no infrastructure-as-code. You're going to build all of that \
yourself, phase by phase:

**A** Containerization · **B** CI · **C** Observability · **D** SLI/SLO · \
**E** Chaos Engineering · **F** CD

This is the same progression a real team follows when taking a service from \
"runs on my laptop" to "production-ready."

---

## Your First Task

Before we touch any infrastructure, confirm the application runs.

**Task: Get all three services up and verify they're healthy.**

1. Check the README for how to run locally
2. Start all three services and PostgreSQL
3. `curl` the health endpoint on each service

Paste your health check output here when all three are green.\
"""


def _drain_remaining() -> str:
    """Read remaining pasted text from all buffer layers.

    After readline(), pasted data lives in two places:
    1. Python's TextIOWrapper internal buffer (complete lines pulled from fd)
    2. Kernel line discipline buffer (last line without trailing newline,
       held back in canonical mode until Enter)

    Strategy: switch to non-canonical mode FIRST (releases kernel-held data),
    then drain Python's buffer char-by-char (safe: reads from internal memory
    until exhausted), then drain the raw fd (catches anything not yet in
    Python's buffers).
    """
    fd = sys.stdin.fileno()

    old_attrs = termios.tcgetattr(fd)
    new_attrs = list(old_attrs)
    new_attrs[3] &= ~termios.ICANON
    new_attrs[6][termios.VMIN] = 0
    new_attrs[6][termios.VTIME] = 0
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)

    try:
        termios.tcsetattr(fd, termios.TCSANOW, new_attrs)
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)

        # Drain TextIOWrapper's internal decoded buffer (char-by-char is safe —
        # reads from memory, only touches fd when buffer is exhausted)
        chars = []
        try:
            while True:
                ch = sys.stdin.read(1)
                if not ch:
                    break
                chars.append(ch)
        except (BlockingIOError, IOError):
            pass

        # Drain raw fd (kernel buffer, now fully accessible in non-canonical mode)
        try:
            raw = os.read(fd, 65536)
            if raw:
                chars.append(raw.decode(errors="replace"))
        except (BlockingIOError, OSError):
            pass

    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
        termios.tcsetattr(fd, termios.TCSANOW, old_attrs)

    return "".join(chars)


def _read_input() -> str:
    """Read user input, accumulating pasted multi-line text into one message."""
    first_line = console.input("[bold blue]you>[/bold blue] ")
    first_line += "\n"
    if not first_line:
        raise EOFError
    result = first_line.rstrip("\n")
    time.sleep(0.15)
    remaining = _drain_remaining().rstrip("\n")
    if remaining:
        result += "\n" + remaining
    return result


def _detect_quiz_state(response: str, current_state: dict | None) -> dict | None:
    """Detect quiz question or completion markers in assistant response."""
    # "question N of M" or "Question N of M"
    q_match = re.search(r'[Qq]uestion\s+(\d+)\s+of\s+(\d+)', response)
    if q_match:
        asked = int(q_match.group(1))
        total = int(q_match.group(2))
        answered = asked - 1
        return {"total": total, "asked": asked, "answered": answered}
    # "Quiz complete" or "N/N" completion
    if current_state and re.search(r'[Qq]uiz complete', response):
        return None
    return current_state


def _parse_file_requests(response: str) -> list[str]:
    """Extract file paths from <<<FILES ... FILES>>> blocks in mentor response."""
    paths = []
    for match in re.finditer(r'<<<FILES\n(.*?)\nFILES>>>', response, re.DOTALL):
        for line in match.group(1).strip().split('\n'):
            line = line.strip()
            if line:
                paths.append(line)
    return paths


def _parse_write_requests(response: str) -> list[tuple[str, str]]:
    """Extract file writes from <<<WRITE_FILE path\\ncontent\\nWRITE_FILE>>> blocks."""
    writes = []
    for match in re.finditer(r'<<<WRITE_FILE\s+(.+?)\n(.*?)\nWRITE_FILE>>>', response, re.DOTALL):
        path = match.group(1).strip()
        content = match.group(2)
        writes.append((path, content))
    return writes


def _confirm_and_apply_writes(writes: list[tuple[str, str]]) -> list[str]:
    """Show proposed writes, ask for confirmation, apply if approved. Returns list of written paths."""
    from pathlib import Path
    written = []
    apply_all = False

    if len(writes) > 1:
        console.print(f"\n[bold yellow]{len(writes)} file(s) to write:[/bold yellow]")
        for path, _ in writes:
            console.print(f"  {Path(path).resolve()}")
        batch = console.input("[bold]Apply all? [y/N/review]: [/bold]").strip().lower()
        if batch in ("y", "yes"):
            apply_all = True
        elif batch in ("n", "no"):
            return written

    for path, content in writes:
        resolved = Path(path).resolve()
        if not apply_all:
            console.print(f"\n[bold yellow]Proposed write:[/bold yellow] {resolved}")
            lines = content.split('\n')
            preview = '\n'.join(lines[:20])
            if len(lines) > 20:
                preview += f"\n... ({len(lines) - 20} more lines)"
            console.print(Panel(preview, border_style="yellow", title="content"))
            confirm = console.input("[bold]Apply this change? [y/N]: [/bold]").strip().lower()
            if confirm not in ("y", "yes"):
                console.print("[dim]Skipped.[/dim]")
                continue

        if write_file_content(path, content):
            console.print(f"[green]Written:[/green] {resolved}")
            written.append(path)
        else:
            console.print(f"[red]Failed to write:[/red] {resolved}")
    return written


def _has_phase_complete(response: str) -> bool:
    """Check if mentor signaled phase completion."""
    return "<<<PHASE_COMPLETE>>>" in response


def _parse_chaos_injection(response: str) -> str | None:
    """Extract chaos scenario from <<<CHAOS scenario_name>>> marker."""
    match = re.search(r'<<<CHAOS\s+(\w+)>>>', response)
    return match.group(1) if match else None


def _parse_progress_markers(response: str) -> list[tuple[str, str]]:
    """Extract progress markers: (item_type, item_key) pairs."""
    markers = []
    for match in re.finditer(r'<<<TASK_DONE\s+(\d+)>>>', response):
        markers.append(("task", match.group(1)))
    for match in re.finditer(r'<<<QUIZ_DONE\s+(\d+)>>>', response):
        markers.append(("quiz", match.group(1)))
    for match in re.finditer(r'<<<SCENARIO_DONE\s+(\d+)>>>', response):
        markers.append(("scenario", match.group(1)))
    return markers


NEXUS_GATEWAY_URL = os.environ.get("NEXUS_GATEWAY_URL", "http://localhost:8000")
NEXUS_OPS_TOKEN = os.environ.get("NEXUS_OPS_TOKEN", "br-labs-ops-7f3a2b")


def _inject_chaos(scenario: str) -> tuple[bool, str]:
    """Silently inject a chaos scenario into the learner's running services."""
    import httpx
    url = f"{NEXUS_GATEWAY_URL}/ops/{NEXUS_OPS_TOKEN}/{scenario}/start"
    try:
        resp = httpx.post(url, json={}, timeout=10.0)
        if resp.status_code == 200:
            return True, resp.json().get("status", "started")
        return False, f"HTTP {resp.status_code}: {resp.text}"
    except httpx.ConnectError:
        return False, "Could not reach nexus-fx gateway"
    except Exception as e:
        return False, str(e)


def _stop_chaos(scenario: str) -> bool:
    """Stop a running chaos scenario."""
    import httpx
    url = f"{NEXUS_GATEWAY_URL}/ops/{NEXUS_OPS_TOKEN}/{scenario}/stop"
    try:
        resp = httpx.post(url, timeout=10.0)
        return resp.status_code == 200
    except Exception:
        return False


def _strip_markers(response: str) -> str:
    """Remove all structured markers and hallucinated user responses from display/history text."""
    text = re.sub(r'\n*<<<FILES\n.*?\nFILES>>>\n*', '', response, flags=re.DOTALL)
    text = re.sub(r'\n*<<<WRITE_FILE\s+.+?\n.*?\nWRITE_FILE>>>\n*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n*<<<PHASE_COMPLETE>>>\n*', '', text)
    text = re.sub(r'\n*<<<CHAOS\s+\w+>>>\n*', '', text)
    text = re.sub(r'\n*<<<CHAOS_STOP\s+\w+>>>\n*', '', text)
    text = re.sub(r'\n*<<<TASK_DONE\s+\d+>>>\n*', '', text)
    text = re.sub(r'\n*<<<QUIZ_DONE\s+\d+>>>\n*', '', text)
    text = re.sub(r'\n*<<<SCENARIO_DONE\s+\d+>>>\n*', '', text)
    # Truncate at hallucinated user responses
    text = re.split(r'\n+(?:User|user)\s*:', text, maxsplit=1)[0]
    return text


def _read_requested_files(paths: list[str]) -> str:
    """Read files from the learner's project and format for the mentor."""
    sections = []
    for path in paths:
        content = read_file_content(path)
        if content:
            sections.append(f"--- {path} ---\n{content}")
        else:
            sections.append(f"--- {path} ---\n[File not found]")
    return "\n\n".join(sections)


def _refresh_file_context(static_context: str | None) -> str | None:
    """Combine static file context with fresh git state."""
    sections = []
    if static_context:
        sections.append(static_context)
    diff = get_git_diff()
    if diff:
        sections.append(f"--- Git Diff ---\n{diff}")
    status = get_git_status()
    if status:
        sections.append(f"--- Git Status ---\n{status}")
    return "\n\n".join(sections) if sections else None


def _extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Extract fenced code blocks from markdown. Returns list of (lang, code)."""
    return re.findall(r'```(\w*)\n(.*?)```', text, re.DOTALL)


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard via pbcopy (macOS)."""
    try:
        proc = subprocess.run(
            ["pbcopy"], input=text.strip(), text=True, timeout=5,
            capture_output=True,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _offer_copy(code_blocks: list[tuple[str, str]]):
    """Prompt user to copy code blocks to clipboard."""
    commands = [(lang, code.strip()) for lang, code in code_blocks if code.strip()]
    if not commands:
        return
    if len(commands) == 1:
        lang, code = commands[0]
        preview = code if len(code) < 120 else code[:120] + "..."
        console.print(f"[dim]📋 {preview}[/dim]")
        choice = console.input("[dim]Copy to clipboard? [y/N]: [/dim]").strip().lower()
        if choice in ("y", "yes"):
            if _copy_to_clipboard(code):
                console.print("[dim]Copied.[/dim]")
            else:
                console.print("[dim]Copy failed — pbcopy not available.[/dim]")
    else:
        console.print(f"[dim]📋 {len(commands)} code block(s) found:[/dim]")
        for i, (lang, code) in enumerate(commands, 1):
            preview = code.split('\n')[0]
            if len(preview) > 80:
                preview = preview[:80] + "..."
            console.print(f"[dim]  {i}. {preview}[/dim]")
        choice = console.input(f"[dim]Copy which? [1-{len(commands)}/a=all/N]: [/dim]").strip().lower()
        if choice in ("a", "all"):
            combined = "\n\n".join(code for _, code in commands)
            if _copy_to_clipboard(combined):
                console.print("[dim]Copied all.[/dim]")
        elif choice.isdigit() and 1 <= int(choice) <= len(commands):
            if _copy_to_clipboard(commands[int(choice) - 1][1]):
                console.print("[dim]Copied.[/dim]")


PHASE_NAMES = {
    "containerization": "A (Containerization)",
    "ci": "B (CI)",
    "observability": "C (Observability)",
    "slo": "D (SLI/SLO)",
    "chaos": "E (Chaos Engineering)",
    "cd": "F (CD to AWS)",
}

_active_client: "MentorClient | None" = None


def _build_progress_summary(client: MentorClient, phase: str) -> str:
    """Fetch progress from server and format as context for the mentor."""
    progress = client.get_progress()
    if not progress:
        return ""
    items = progress.get("items", [])
    if not items:
        return f"\n[PROGRESS: Phase {PHASE_NAMES.get(phase, phase)}, no completed items yet.]"

    by_phase: dict[str, list[str]] = {}
    for item in items:
        p = item["phase"]
        by_phase.setdefault(p, []).append(f"{item['item_type']}:{item['item_key']}")

    lines = []
    for p in ["containerization", "ci", "observability", "slo", "chaos", "cd"]:
        if p in by_phase:
            lines.append(f"  {PHASE_NAMES.get(p, p)}: {len(by_phase[p])} items complete")
    summary = "\n".join(lines)
    return f"\n[PROGRESS: Current phase = {PHASE_NAMES.get(phase, phase)}. Completed:\n{summary}]"


def _sync_session(messages: list[dict], phase: str, quiz_state: dict | None = None) -> None:
    """Save session locally and push to server."""
    save_session(messages, phase, quiz_state)
    if _active_client:
        _active_client.push_session(messages, phase, quiz_state)


def _render_response(stream: Iterator[str], status: str = "Thinking...") -> str:
    """Collect streamed chunks with a spinner, then render as Markdown."""
    full_response = ""
    with Live(Spinner("dots", text=status), console=console, transient=True):
        for chunk in stream:
            full_response += chunk
    display_text = _strip_markers(full_response).strip()
    if display_text:
        console.print(Panel(Markdown(display_text), border_style="green", title="mentor"))
        code_blocks = _extract_code_blocks(display_text)
        if code_blocks:
            _offer_copy(code_blocks)
    return full_response


@auth_app.command("login")
def auth_login(
    server_url: str = typer.Option(
        None, "--server", "-s", envvar="BR_SERVER_URL",
        help=f"Server URL (default: {DEFAULT_SERVER_URL})",
    ),
    token: str = typer.Option(
        None, "--token", "-t",
        help="Provide a token directly (skip interactive login)",
    ),
):
    """Authenticate with the Blast Radius server."""
    url = server_url or DEFAULT_SERVER_URL
    login_flow(url, token)
    console.print("[green]Authenticated successfully.[/green]")


@auth_app.command("status")
def auth_status():
    """Check current authentication status."""
    token = get_token()
    if token:
        console.print("[green]Authenticated.[/green] Token is stored locally.")
    else:
        console.print("[yellow]Not authenticated.[/yellow] Run: br-mentor auth login")


@auth_app.command("logout")
def auth_logout():
    """Clear stored credentials and session history."""
    token = get_token()
    url = get_server_url() or DEFAULT_SERVER_URL
    if token:
        MentorClient(base_url=url, token=token).clear_remote_session()
    clear_auth()
    clear_session()
    console.print("[green]Logged out.[/green] Credentials and session cleared.")


@app.command()
def chat(
    context_files: list[str] = typer.Option(
        [], "--context", "-c",
        help="Files to always include as context for the mentor",
    ),
    new: bool = typer.Option(
        False, "--new", "-n",
        help="Start a fresh session (clear previous history)",
    ),
    server_url: str = typer.Option(
        None, "--server", "-s", envvar="BR_SERVER_URL",
        help="Server URL",
    ),
):
    """Start an interactive chat session with the SRE mentor."""
    url = server_url or get_server_url() or DEFAULT_SERVER_URL
    token = get_token()
    if not token:
        console.print("[yellow]Not authenticated. Let's fix that.[/yellow]")
        login_flow(url)
        token = get_token()
        console.print("[green]Authenticated successfully.[/green]\n")

    client = MentorClient(base_url=url, token=token)
    global _active_client
    _active_client = client

    # Gather static file context (from --context flag only; git state refreshes per message)
    static_context = gather_context(context_files, include_git_diff=False)

    if new:
        clear_session()
        client.clear_remote_session()

    console.print(
        Panel(
            "[bold]Blast Radius[/bold] - AI-mentored learning session\n"
            "Type your message and press Enter. Use 'quit' or Ctrl+C to exit.\n"
            "Use --new to start a fresh session.",
            border_style="blue",
        )
    )

    if static_context:
        console.print(f"[dim]Attached context: {len(static_context)} chars[/dim]")

    # Try server session first (portable across machines), fall back to local
    # only if server is unreachable. If server says "no session," that's authoritative.
    remote = client.pull_session() if not new else None
    if remote and remote.get("messages"):
        previous_messages = remote["messages"]
        phase = remote["phase"]
        quiz_state = remote.get("quiz_state")
        save_session(previous_messages, phase, quiz_state)
        console.print("[dim]Session restored from server.[/dim]")
    elif remote is not None:
        # Server responded but user has no session — fresh start
        previous_messages, phase, quiz_state = [], "containerization", None
        clear_session()
    else:
        # Server unreachable — fall back to local cache
        previous_messages, phase, quiz_state = load_session()

    has_real_history = any(m.get("role") == "user" for m in previous_messages)

    if has_real_history:
        client.report_phase(phase)

    if has_real_history and not new:
        messages = previous_messages
        console.print(f"[dim]Phase: {phase}[/dim]")
        if quiz_state:
            console.print(f"[dim]Quiz in progress: {quiz_state['answered']}/{quiz_state['total']} answered[/dim]")
            kickoff = (
                "I'm picking up where I left off. "
                f"[QUIZ STATE: {quiz_state['asked']} of {quiz_state['total']} questions asked, "
                f"{quiz_state['answered']} answered. Resume the quiz from question {quiz_state['answered'] + 1}.]"
            )
        else:
            progress_ctx = _build_progress_summary(client, phase)
            kickoff = (
                "I'm picking up where I left off. Give me a brief summary of "
                "where we are and what my next step is."
                f"{progress_ctx}"
            )
        messages.append({"role": "user", "content": kickoff})
        try:
            file_context = _refresh_file_context(static_context)
            full_response = _render_response(
                client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                status="Loading session history...",
            )
        except Exception as e:
            console.print(f"\n[red]Error connecting to mentor: {e}[/red]")
            raise SystemExit(1)
        clean_kickoff = _strip_markers(full_response)
        messages.append({"role": "assistant", "content": clean_kickoff})

        # Process chaos injection from kickoff response (e.g., resuming mid-phase)
        chaos_scenario = _parse_chaos_injection(full_response)
        if chaos_scenario:
            ok, detail = _inject_chaos(chaos_scenario)
            if ok:
                auto_msg = "[SYSTEM: Chaos scenario injected successfully. The learner does not know what was injected. Begin the incident.]"
            else:
                auto_msg = f"[SYSTEM: Chaos injection failed — {detail}. Inform the learner there's a setup issue.]"
            messages.append({"role": "user", "content": auto_msg})
            try:
                file_context = _refresh_file_context(static_context)
                followup = _render_response(
                    client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                    status="Incident starting...",
                )
            except Exception as e:
                console.print(f"\n[red]Error: {e}[/red]")
                messages.pop()
                _sync_session(messages, phase, quiz_state)
                raise SystemExit(1)
            clean_followup = _strip_markers(followup)
            messages.append({"role": "assistant", "content": clean_followup})
    else:
        messages = []
        quiz_state = None
        # Check if user has existing progress on the server (e.g. session lost but
        # progress intact) — don't reset them to phase A
        server_phase = None
        try:
            import httpx
            me = httpx.get(f"{client.base_url}/auth/me", headers=client._headers(), timeout=5.0)
            if me.status_code == 200:
                server_phase = me.json().get("phase")
        except Exception:
            pass
        if server_phase and server_phase != "containerization":
            phase = server_phase
            console.print(f"[dim]Session history lost, but your progress is intact at phase: {phase}[/dim]")
            progress_ctx = _build_progress_summary(client, phase)
            kickoff = (
                f"I'm resuming at phase {PHASE_NAMES.get(phase, phase)} but my conversation history was lost. "
                f"Give me my next task for this phase — don't repeat what I've already done."
                f"{progress_ctx}"
            )
            messages.append({"role": "user", "content": kickoff})
            try:
                file_context = _refresh_file_context(static_context)
                full_response = _render_response(
                    client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                    status=f"Resuming {phase}...",
                )
            except Exception as e:
                console.print(f"\n[red]Error connecting to mentor: {e}[/red]")
                raise SystemExit(1)
            clean = _strip_markers(full_response)
            messages.append({"role": "assistant", "content": clean})
        else:
            phase = "containerization"
            console.print(Panel(Markdown(WELCOME_MESSAGE), border_style="green", title="mentor"))
            messages.append({"role": "assistant", "content": WELCOME_MESSAGE})

    _sync_session(messages, phase, quiz_state)

    while True:
        try:
            user_input = _read_input()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session ended.[/dim]")
            break

        if user_input.strip().lower() in ("quit", "exit", "q"):
            console.print("[dim]Session ended.[/dim]")
            break

        if not user_input.strip():
            console.print()
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            file_context = _refresh_file_context(static_context)
            latest_response = _render_response(client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state))
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")
            messages.pop()
            continue

        clean_response = _strip_markers(latest_response)
        messages.append({"role": "assistant", "content": clean_response})

        quiz_state = _detect_quiz_state(clean_response, quiz_state)

        # Process structured actions from the response. Each action can
        # trigger a round-trip that produces a new response, which may
        # itself contain actions — so we loop until there's nothing left.
        for _guard in range(10):
            acted = False

            # File reads
            requested_files = _parse_file_requests(latest_response)
            if requested_files:
                acted = True
                from pathlib import Path
                console.print(f"[dim]Reading {len(requested_files)} file(s):[/dim]")
                for p in requested_files:
                    console.print(f"[dim]  {Path(p).resolve()}[/dim]")
                file_contents = _read_requested_files(requested_files)
                auto_msg = f"[Attached files from project]\n\n{file_contents}"
                messages.append({"role": "user", "content": auto_msg})
                try:
                    file_context = _refresh_file_context(static_context)
                    latest_response = _render_response(
                        client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                        status="Reviewing...",
                    )
                except Exception as e:
                    console.print(f"\n[red]Error during review: {e}[/red]")
                    messages.pop()
                    _sync_session(messages, phase, quiz_state)
                    break
                clean = _strip_markers(latest_response)
                messages.append({"role": "assistant", "content": clean})
                continue

            # File writes
            proposed_writes = _parse_write_requests(latest_response)
            if proposed_writes:
                acted = True
                written_paths = _confirm_and_apply_writes(proposed_writes)
                if written_paths:
                    auto_msg = f"[Files written: {', '.join(written_paths)}]"
                    messages.append({"role": "user", "content": auto_msg})
                    try:
                        file_context = _refresh_file_context(static_context)
                        latest_response = _render_response(
                            client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                            status="Continuing...",
                        )
                    except Exception as e:
                        console.print(f"\n[red]Error: {e}[/red]")
                        messages.pop()
                        _sync_session(messages, phase, quiz_state)
                        break
                    clean = _strip_markers(latest_response)
                    messages.append({"role": "assistant", "content": clean})
                    continue

            # Chaos injection (silent — learner never sees this)
            chaos_scenario = _parse_chaos_injection(latest_response)
            if chaos_scenario:
                acted = True
                ok, detail = _inject_chaos(chaos_scenario)
                if ok:
                    auto_msg = "[SYSTEM: Chaos scenario injected successfully. The learner does not know what was injected. Begin the incident.]"
                else:
                    auto_msg = f"[SYSTEM: Chaos injection failed — {detail}. Inform the learner there's a setup issue.]"
                messages.append({"role": "user", "content": auto_msg})
                try:
                    file_context = _refresh_file_context(static_context)
                    latest_response = _render_response(
                        client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                        status="Incident starting...",
                    )
                except Exception as e:
                    console.print(f"\n[red]Error: {e}[/red]")
                    messages.pop()
                    _sync_session(messages, phase, quiz_state)
                    break
                clean = _strip_markers(latest_response)
                messages.append({"role": "assistant", "content": clean})
                continue

            # Chaos stop — just stop the scenario silently, no follow-up API call.
            # The mentor's debrief question is already in this response; the learner
            # answers on their next turn.
            chaos_stop = re.search(r'<<<CHAOS_STOP\s+(\w+)>>>', latest_response)
            if chaos_stop:
                _stop_chaos(chaos_stop.group(1))

            # Progress tracking — report task/quiz/scenario completions to server
            for item_type, item_key in _parse_progress_markers(latest_response):
                client.report_progress(phase, item_type, item_key)

            # Phase completion
            if _has_phase_complete(latest_response):
                old_phase = phase
                phase = advance_phase(phase)
                _sync_session(messages, phase, quiz_state)
                if phase != old_phase:
                    console.print(
                        f"\n[bold green]Phase complete![/bold green] "
                        f"Advancing: {old_phase} → {phase}\n"
                    )
                    client.report_phase(phase)
                    acted = True
                    kickoff = (
                        f"I'm ready for the next phase. "
                        f"Give me the first task for {phase}."
                    )
                    messages.append({"role": "user", "content": kickoff})
                    try:
                        file_context = _refresh_file_context(static_context)
                        latest_response = _render_response(
                            client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                            status=f"Starting {phase}...",
                        )
                    except Exception as e:
                        console.print(f"\n[red]Error: {e}[/red]")
                        messages.pop()
                        _sync_session(messages, phase, quiz_state)
                        break
                    clean = _strip_markers(latest_response)
                    messages.append({"role": "assistant", "content": clean})
                    continue
                else:
                    console.print(
                        "\n[bold green]All phases complete. "
                        "Congratulations![/bold green]\n"
                    )

            if not acted:
                break

        _sync_session(messages, phase, quiz_state)


@app.command()
def ask(
    message: str = typer.Argument(help="A single question to ask the mentor"),
    context_files: list[str] = typer.Option(
        [], "--context", "-c",
        help="Files to include as context",
    ),
    server_url: str = typer.Option(
        None, "--server", "-s", envvar="BR_SERVER_URL",
    ),
):
    """Send a single message to the mentor (non-interactive)."""
    url = server_url or get_server_url() or DEFAULT_SERVER_URL
    token = get_token()
    if not token:
        console.print("[yellow]Not authenticated. Let's fix that.[/yellow]")
        login_flow(url)
        token = get_token()
        console.print("[green]Authenticated successfully.[/green]\n")

    client = MentorClient(base_url=url, token=token)
    file_context = gather_context(context_files, include_git_diff=False)

    messages = [{"role": "user", "content": message}]
    full_response = ""

    for chunk in client.chat_stream(messages, file_context):
        full_response += chunk

    console.print(Markdown(full_response))


@app.command()
def usage(
    server_url: str = typer.Option(
        None, "--server", "-s", envvar="BR_SERVER_URL",
    ),
):
    """Show cumulative token usage and estimated cost for this session."""
    url = server_url or get_server_url() or DEFAULT_SERVER_URL
    token = get_token()
    if not token:
        console.print("[yellow]Not authenticated.[/yellow] Run: br-mentor auth login")
        raise SystemExit(1)

    client = MentorClient(base_url=url, token=token)
    try:
        data = client.get_usage()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)

    from rich.table import Table

    console.print(f"\n[bold]Model:[/bold] {data['model']}")
    console.print(f"[dim]Rates: ${data['rates_per_mtok']['input']}/MTok input, ${data['rates_per_mtok']['output']}/MTok output[/dim]\n")

    table = Table(title="Usage by Phase")
    table.add_column("Phase", style="cyan")
    table.add_column("Requests", justify="right")
    table.add_column("Input Tokens", justify="right")
    table.add_column("Output Tokens", justify="right")
    table.add_column("Cost", justify="right", style="green")

    for phase, stats in data.get("by_phase", {}).items():
        table.add_row(
            phase,
            str(stats["requests"]),
            f"{stats['input_tokens']:,}",
            f"{stats['output_tokens']:,}",
            f"${stats['cost_usd']:.4f}",
        )

    totals = data.get("session_total", {})
    table.add_section()
    table.add_row(
        "[bold]Total[/bold]",
        str(totals.get("requests", 0)),
        f"{totals.get('input_tokens', 0):,}",
        f"{totals.get('output_tokens', 0):,}",
        f"[bold]${totals.get('cost_usd', 0):.4f}[/bold]",
    )

    console.print(table)


if __name__ == "__main__":
    app()
