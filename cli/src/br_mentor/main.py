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
from br_mentor.context import gather_context, read_file_content, write_file_content, get_git_diff, get_git_status, get_changed_files
from br_mentor.session import advance_phase, clear_session, load_session, save_session

app = typer.Typer(
    name="br-mentor",
    help="AI-mentored SRE learning platform CLI",
    no_args_is_help=True,
)
auth_app = typer.Typer(help="Authentication commands")
app.add_typer(auth_app, name="auth")

console = Console()

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
    # Quiz completion: "Quiz complete", "Quiz score: N/N", "Phase X complete"
    if current_state and re.search(
        r'[Qq]uiz (complete|score:\s*\d+/\d+)|[Pp]hase\s+\w+\s+complete', response
    ):
        return None
    return current_state


def _report_quiz_from_state(
    client, phase: str,
    prev: dict | None, current: dict | None,
) -> None:
    """Report quiz completions by comparing quiz state transitions.

    When the mentor asks "Question N of M", _detect_quiz_state sets
    answered = N-1.  If answered increased, the questions in between
    were just graded — report them.  If state went to None (quiz
    complete), report the final question.
    """
    prev_answered = prev.get("answered", 0) if prev else 0
    if current is None:
        if prev is None:
            return
        # Quiz just completed — report from prev_answered+1 to total
        for q in range(prev_answered + 1, prev.get("total", 0) + 1):
            client.report_progress(phase, "quiz", str(q))
    elif current.get("answered", 0) > prev_answered:
        for q in range(prev_answered + 1, current["answered"] + 1):
            client.report_progress(phase, "quiz", str(q))


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


_REVIEW_INTENT_RE = re.compile(
    r'(?:let me (?:review|look|check|see|pull up|examine|inspect|read|take a look|grab|open)'
    r'|I\'ll (?:review|check|look|examine|inspect|read|take a look|grab|open|pull up)'
    r'|looking at (?:your|the) '
    r'|reviewing (?:your|the) '
    r'|let me .{0,20}(?:file|workflow|config|code|yml|yaml|dockerfile))',
    re.IGNORECASE,
)


def _has_review_intent_without_files(response: str) -> bool:
    """Detect when the mentor intends to review but forgot the <<<FILES block."""
    if '<<<FILES' in response:
        return False
    return bool(_REVIEW_INTENT_RE.search(response))


def _get_learner_changed_files() -> list[str]:
    """Get changed files that are plausibly learner work (not CLI internals)."""
    return [
        f for f in get_changed_files()
        if not f.startswith("cli/")
    ]


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
    """Check if server signaled phase completion via SSE event."""
    return "[PHASE_COMPLETE]" in response


def _parse_chaos_injection(response: str) -> str | None:
    """Extract chaos scenario from <<<CHAOS scenario_name>>> marker."""
    match = re.search(r'<<<CHAOS\s+(\w+)>>>', response)
    return match.group(1) if match else None


def _parse_progress_markers(response: str) -> list[tuple[str, str]]:
    """Extract progress signals from server SSE events."""
    markers = []
    for match in re.finditer(r'\[PROGRESS (task|quiz|scenario) (\d+)\]', response):
        markers.append((match.group(1), match.group(2)))
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
    text = re.sub(r'\n*<<<PHASE_COMPLETE[^>]*>>>\n*', '', text)
    text = re.sub(r'\n*<<<CHAOS\s+\w+>>>\n*', '', text)
    text = re.sub(r'\n*<<<CHAOS_STOP\s+\w+>>>\n*', '', text)
    text = re.sub(r'\n*<<<TASK_DONE[^>]*>>>\n*', '', text)
    text = re.sub(r'\n*<<<QUIZ_DONE[^>]*>>>\n*', '', text)
    text = re.sub(r'\n*<<<SCENARIO_DONE[^>]*>>>\n*', '', text)
    text = re.sub(r'\[PROGRESS (?:task|quiz|scenario) \d+\]', '', text)
    text = re.sub(r'\[PHASE_COMPLETE\]', '', text)
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
        result = f"\n[PROGRESS: Phase {PHASE_NAMES.get(phase, phase)}, no completed items yet.]"
    else:
        by_phase: dict[str, dict[str, list[str]]] = {}
        for item in items:
            p = item["phase"]
            t = item["item_type"]
            by_phase.setdefault(p, {}).setdefault(t, []).append(item["item_key"])

        lines = []
        for p in ["containerization", "ci", "observability", "slo", "chaos", "cd"]:
            if p not in by_phase:
                continue
            parts = []
            for t in ["task", "quiz", "scenario"]:
                if t in by_phase[p]:
                    keys = by_phase[p][t]
                    label = {"task": "tasks", "quiz": "quiz questions", "scenario": "scenarios"}[t]
                    parts.append(f"{label} {','.join(sorted(keys))} complete")
            lines.append(f"  {PHASE_NAMES.get(p, p)}: {'; '.join(parts)}")

        current = by_phase.get(phase, {})
        current_quiz = current.get("quiz", [])
        current_scenarios = current.get("scenario", [])
        current_tasks = current.get("task", [])
        phase_done = False
        if phase == "chaos":
            phase_done = len(current_quiz) >= 4 and len(current_scenarios) >= 3
        elif current_quiz and current_tasks:
            phase_done = len(current_quiz) >= 4 and len(current_tasks) >= 4

        summary = "\n".join(lines)
        if phase_done:
            result = (
                f"\n[PROGRESS: Current phase = {PHASE_NAMES.get(phase, phase)}. "
                f"ALL REQUIRED ITEMS COMPLETE — this phase is finished. "
                f"The learner should advance to the next phase. Completed:\n{summary}]"
            )
        else:
            result = f"\n[PROGRESS: Current phase = {PHASE_NAMES.get(phase, phase)}. Completed:\n{summary}]"

    if phase == "cd":
        aws = client.get_aws_status()
        if aws and aws.get("status") == "ready":
            result += (
                f"\n[AWS ACCOUNT PROVISIONED: account_id={aws['account_id']}, "
                f"iam_username={aws['iam_username']}, "
                f"state_bucket={aws['state_bucket']}, "
                f"lock_table={aws['lock_table']}. "
                f"Credentials are on the learner's dashboard.]"
            )
        elif aws and aws.get("status") == "provisioning":
            result += "\n[AWS ACCOUNT STATUS: provisioning in progress.]"

    return result


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
        help="Server URL (default: https://blastradiuslab.com)",
    ),
    token: str = typer.Option(
        None, "--token", "-t",
        help="Provide a token directly (skip interactive login)",
    ),
):
    """Authenticate with the Blast Radius server."""
    url = server_url or "https://blastradiuslab.com"
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
    url = get_server_url() or "https://blastradiuslab.com"
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
    url = server_url or get_server_url() or "https://blastradiuslab.com"
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

    tier_gated = False

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
            if "ALL REQUIRED ITEMS COMPLETE" in progress_ctx:
                kickoff = (
                    f"I've completed all required items in {PHASE_NAMES.get(phase, phase)}. "
                    f"Give me a brief congratulatory summary of what I accomplished, "
                    f"then advance me to the next phase. "
                    f"Do NOT re-quiz me — the quiz is already done. "
                    f"Emit <<<PHASE_COMPLETE>>> in your response to trigger the transition."
                    f"{progress_ctx}"
                )
            else:
                kickoff = (
                    "I'm picking up where I left off. Give me a brief summary of "
                    "where we are and what my next step is."
                    f"{progress_ctx}"
                )
        # Include changed files directly so the model can review
        # without needing a round-trip file request.
        changed = _get_learner_changed_files()
        if changed:
            file_contents = _read_requested_files(changed)
            kickoff += f"\n\n[Changed files attached for review]\n\n{file_contents}"

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

        # Handle file actions from the resume response
        _resume_resp = full_response
        for _ in range(5):
            req_files = _parse_file_requests(_resume_resp)
            if not req_files and _has_review_intent_without_files(_resume_resp):
                req_files = _get_learner_changed_files()
                if req_files:
                    console.print(f"[dim]Auto-attaching {len(req_files)} changed file(s):[/dim]")
            if not req_files:
                break
            from pathlib import Path as _P
            for _rp in req_files:
                console.print(f"[dim]  {_P(_rp).resolve()}[/dim]")
            _fc = _read_requested_files(req_files)
            messages.append({"role": "user", "content": f"[Attached files from project]\n\n{_fc}"})
            try:
                file_context = _refresh_file_context(static_context)
                _resume_resp = _render_response(
                    client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                    status="Reviewing...",
                )
            except Exception as e:
                console.print(f"\n[red]Error during review: {e}[/red]")
                messages.pop()
                break
            clean = _strip_markers(_resume_resp)
            messages.append({"role": "assistant", "content": clean})
        full_response = _resume_resp

        # Phase advance from kickoff (e.g., returning after completing a phase)
        if _has_phase_complete(full_response):
            old_phase = phase
            next_phase = advance_phase(phase)
            if next_phase != old_phase:
                if client.report_phase(next_phase):
                    phase = next_phase
                    console.print(
                        f"\n[bold green]Phase complete![/bold green] "
                        f"Advancing: {old_phase} → {phase}\n"
                    )
                else:
                    tier_gated = True
                    console.print(
                        f"\n[bold green]Phase {old_phase} complete![/bold green]\n"
                        f"[yellow]The next phase requires an upgrade. "
                        f"Visit your dashboard to unlock it.[/yellow]\n"
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM: Phase advance was blocked — the learner's tier "
                            "does not include the next phase. They need to upgrade from "
                            "their dashboard. Do NOT emit <<<PHASE_COMPLETE>>> again. "
                            "Acknowledge the gate and wait for them to upgrade.]"
                        ),
                    })

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
        # Check server for phase and progress — resume even on phase A if
        # items are recorded (e.g. session lost but progress intact).
        server_phase = None
        try:
            import httpx
            me = httpx.get(f"{client.base_url}/auth/me", headers=client._headers(), timeout=5.0)
            if me.status_code == 200:
                server_phase = me.json().get("phase")
        except Exception:
            pass
        phase = server_phase or "containerization"
        progress_ctx = _build_progress_summary(client, phase)
        has_progress = progress_ctx and "no completed items yet" not in progress_ctx

        if has_progress:
            console.print(f"[dim]Resuming at phase: {phase}[/dim]")
            if "ALL REQUIRED ITEMS COMPLETE" in progress_ctx:
                kickoff = (
                    f"Starting a new session. My progress data shows I have completed "
                    f"all required items in {PHASE_NAMES.get(phase, phase)}. "
                    f"Give me a brief congratulatory summary of what I accomplished "
                    f"in this phase, then advance me to the next phase. "
                    f"Do NOT re-quiz me — the quiz is already done. "
                    f"Emit <<<PHASE_COMPLETE>>> in your response to trigger the transition."
                    f"{progress_ctx}"
                )
            else:
                kickoff = (
                    f"Starting a new session. Orient me on where I stand in "
                    f"{PHASE_NAMES.get(phase, phase)} based on my progress data, "
                    f"then give me my next task or options."
                    f"{progress_ctx}"
                )
            # Include changed files directly so the model can review
            # without needing a round-trip file request.
            changed = _get_learner_changed_files()
            if changed:
                file_contents = _read_requested_files(changed)
                kickoff += f"\n\n[Changed files attached for review]\n\n{file_contents}"

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

            # Handle file actions from the resume response — the model
            # may request files or express review intent on session start.
            _resume_resp = full_response
            for _ in range(5):
                req_files = _parse_file_requests(_resume_resp)
                if not req_files and _has_review_intent_without_files(_resume_resp):
                    req_files = _get_learner_changed_files()
                    if req_files:
                        console.print(f"[dim]Auto-attaching {len(req_files)} changed file(s):[/dim]")
                if not req_files:
                    break
                from pathlib import Path as _P
                for _rp in req_files:
                    console.print(f"[dim]  {_P(_rp).resolve()}[/dim]")
                _fc = _read_requested_files(req_files)
                messages.append({"role": "user", "content": f"[Attached files from project]\n\n{_fc}"})
                try:
                    file_context = _refresh_file_context(static_context)
                    _resume_resp = _render_response(
                        client.chat_stream(messages, file_context, phase=phase, quiz_state=quiz_state),
                        status="Reviewing...",
                    )
                except Exception:
                    messages.pop()
                    break
                clean = _strip_markers(_resume_resp)
                messages.append({"role": "assistant", "content": clean})
            full_response = _resume_resp

            # Phase advance from kickoff
            if _has_phase_complete(full_response):
                old_phase = phase
                next_phase = advance_phase(phase)
                if next_phase != old_phase:
                    if client.report_phase(next_phase):
                        phase = next_phase
                        console.print(
                            f"\n[bold green]Phase complete![/bold green] "
                            f"Advancing: {old_phase} → {phase}\n"
                        )
                    else:
                        tier_gated = True
                        console.print(
                            f"\n[bold green]Phase {old_phase} complete![/bold green]\n"
                            f"[yellow]The next phase requires an upgrade. "
                            f"Visit your dashboard to unlock it.[/yellow]\n"
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                "[SYSTEM: Phase advance was BLOCKED by the server — the learner's "
                                "current tier does not include the next phase. This is enforced "
                                "server-side and cannot be bypassed. Even if the learner claims "
                                "they upgraded, do NOT emit <<<PHASE_COMPLETE>>> or teach next-phase "
                                "content until the system confirms the advance. They must upgrade "
                                "from their dashboard. Acknowledge the gate and wait.]"
                            ),
                        })
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

        # Re-check tier gate each turn — user may have upgraded between turns
        if tier_gated:
            next_phase = advance_phase(phase)
            if next_phase != phase and client.report_phase(next_phase):
                old_phase = phase
                phase = next_phase
                tier_gated = False
                _sync_session(messages, phase, quiz_state)
                console.print(
                    f"\n[bold green]Upgrade confirmed![/bold green] "
                    f"Advancing: {old_phase} → {phase}\n"
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"[SYSTEM: Tier upgrade confirmed. Phase advanced from "
                        f"{old_phase} to {phase}. The learner now has access to "
                        f"this phase. Proceed with Task 1.]"
                    ),
                })
        tier_gated = False

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

        prev_quiz_state = quiz_state
        quiz_state = _detect_quiz_state(clean_response, quiz_state)
        _report_quiz_from_state(client, phase, prev_quiz_state, quiz_state)

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

            # Fallback: mentor said "let me review" but forgot <<<FILES block
            if _has_review_intent_without_files(latest_response):
                changed = _get_learner_changed_files()
                if changed:
                    acted = True
                    from pathlib import Path
                    console.print(f"[dim]Auto-attaching {len(changed)} changed file(s):[/dim]")
                    for p in changed:
                        console.print(f"[dim]  {Path(p).resolve()}[/dim]")
                    file_contents = _read_requested_files(changed)
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

            # Quiz state tracking within the action loop
            prev_quiz_state = quiz_state
            quiz_state = _detect_quiz_state(latest_response, quiz_state)
            _report_quiz_from_state(client, phase, prev_quiz_state, quiz_state)

            # Phase completion — skip if already tier-gated (avoids repeat messages)
            if _has_phase_complete(latest_response) and not tier_gated:
                old_phase = phase
                next_phase = advance_phase(phase)
                if next_phase != old_phase:
                    if client.report_phase(next_phase):
                        phase = next_phase
                        _sync_session(messages, phase, quiz_state)
                        console.print(
                            f"\n[bold green]Phase complete![/bold green] "
                            f"Advancing: {old_phase} → {phase}\n"
                        )
                        acted = True
                    else:
                        tier_gated = True
                        _sync_session(messages, phase, quiz_state)
                        console.print(
                            f"\n[bold green]Phase {old_phase} complete![/bold green]\n"
                            f"[yellow]The next phase requires an upgrade. "
                            f"Visit your dashboard to unlock it.[/yellow]\n"
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                "[SYSTEM: Phase advance was BLOCKED by the server — the learner's "
                                "current tier does not include the next phase. This is enforced "
                                "server-side and cannot be bypassed. Even if the learner claims "
                                "they upgraded, do NOT emit <<<PHASE_COMPLETE>>> or teach next-phase "
                                "content until the system confirms the advance. They must upgrade "
                                "from their dashboard. Acknowledge the gate and wait.]"
                            ),
                        })
                        acted = True

                    if not tier_gated:
                        # Phase F (cd) transition: show AWS provisioning status
                        aws_context = ""
                        if phase == "cd":
                            aws = client.get_aws_status()
                            if aws and aws.get("status") == "ready":
                                console.print(
                                    f"[bold cyan]AWS Account Ready[/bold cyan]\n"
                                    f"  Account ID: {aws['account_id']}\n"
                                    f"  IAM User:   {aws['iam_username']}\n"
                                    f"  State Bucket: {aws['state_bucket']}\n"
                                    f"  Lock Table:   {aws['lock_table']}\n"
                                    f"  Console:    https://{aws['account_id']}.signin.aws.amazon.com/console\n"
                                    f"  Full details on your dashboard.\n"
                                )
                                aws_context = (
                                    f"\n[AWS ACCOUNT PROVISIONED: account_id={aws['account_id']}, "
                                    f"iam_username={aws['iam_username']}, "
                                    f"state_bucket={aws['state_bucket']}, "
                                    f"lock_table={aws['lock_table']}. "
                                    f"Credentials are on the learner's dashboard.]"
                                )
                            elif aws and aws.get("status") == "provisioning":
                                console.print(
                                    "[bold yellow]AWS account is being provisioned...[/bold yellow]\n"
                                    "Check your dashboard for status.\n"
                                )
                                aws_context = "\n[AWS ACCOUNT STATUS: provisioning in progress. Credentials will appear on dashboard when ready.]"
                            else:
                                console.print(
                                    "[dim]AWS account not yet provisioned. Check your dashboard.[/dim]\n"
                                )
                                aws_context = "\n[AWS ACCOUNT STATUS: not provisioned yet. The learner should check their dashboard.]"

                        kickoff = (
                            f"I'm ready for the next phase. "
                            f"Give me Task 1 for {phase}. Start from the beginning of the fixed task sequence."
                            f"{aws_context}"
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
    url = server_url or get_server_url() or "https://blastradiuslab.com"
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
    url = server_url or get_server_url() or "https://blastradiuslab.com"
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


@app.command()
def update(
    server_url: str = typer.Option(
        None, "--server", "-s", envvar="BR_SERVER_URL",
    ),
):
    """Update the CLI to the latest version from the server."""
    import shutil
    import tempfile
    import zipfile
    from pathlib import Path

    import httpx

    from br_mentor import CLI_PROTOCOL_VERSION, __version__

    url = server_url or get_server_url() or "https://blastradiuslab.com"

    console.print(f"[dim]Current CLI: v{__version__} (protocol {CLI_PROTOCOL_VERSION})[/dim]")
    console.print(f"[dim]Checking {url} for updates...[/dim]")

    try:
        version_resp = httpx.get(f"{url}/cli/version", timeout=10.0)
        version_resp.raise_for_status()
        version_info = version_resp.json()
    except Exception as e:
        console.print(f"[red]Failed to check for updates: {e}[/red]")
        raise SystemExit(1)

    min_version = version_info.get("min_version", 0)
    if CLI_PROTOCOL_VERSION >= min_version and min_version > 0:
        console.print("[green]CLI is already up to date.[/green]")
        return

    if not version_info.get("update_available"):
        console.print("[yellow]Server does not have a CLI package available.[/yellow]")
        raise SystemExit(1)

    console.print("[bold]Downloading update...[/bold]")
    try:
        pkg_resp = httpx.get(f"{url}/cli/package", timeout=30.0)
        pkg_resp.raise_for_status()
    except Exception as e:
        console.print(f"[red]Failed to download update: {e}[/red]")
        raise SystemExit(1)

    cli_dir = Path(__file__).resolve().parents[2]
    if not (cli_dir / "pyproject.toml").exists():
        console.print(f"[red]Cannot locate CLI directory at {cli_dir}[/red]")
        raise SystemExit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "cli-package.zip"
        zip_path.write_bytes(pkg_resp.content)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)

        src_dir = Path(tmpdir) / "cli"
        if not src_dir.exists():
            console.print("[red]Invalid package structure.[/red]")
            raise SystemExit(1)

        new_pyproject = src_dir / "pyproject.toml"
        if new_pyproject.exists():
            shutil.copy2(new_pyproject, cli_dir / "pyproject.toml")

        new_src = src_dir / "src" / "br_mentor"
        dest_src = cli_dir / "src" / "br_mentor"
        if new_src.exists():
            for py_file in new_src.glob("*.py"):
                shutil.copy2(py_file, dest_src / py_file.name)
                console.print(f"  [dim]Updated {py_file.name}[/dim]")

    console.print("[bold]Reinstalling CLI...[/bold]")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(cli_dir), "-q"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]pip install failed:[/red]\n{result.stderr}")
        raise SystemExit(1)

    console.print("[bold green]CLI updated successfully.[/bold green]")
    console.print("[dim]Restart your session to use the new version.[/dim]")


if __name__ == "__main__":
    app()
