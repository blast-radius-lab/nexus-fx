# fmt: off
# ruff: noqa
"""Phase E (Chaos) QA runner — learner triages real incidents against a live Docker stack.

Unlike qa_runner.py (Phases A-D), this runner:
- Emulates CLI v21 chaos flow: CLI drives injection/stopping on SCENARIO_DONE + stall detection
- Gives opus REAL docker logs, metrics, and API responses to reason over
- Learner executes actual commands via [RUN: ...] blocks in its responses

Prerequisites:
- Docker stack running: docker compose up -d --build (in nexus-fx3)
- Dev server running: localhost:8081
- test8 account at Senior tier
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

CLI_PROTOCOL_VERSION = 21

AUTH_FILE = Path.home() / ".config" / "br-mentor" / "auth.json"
PROJECT_ROOT = Path(__file__).parent

MAX_EXCHANGES = 60

token_usage = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "cost_usd": 0.0}
STALL_LIMIT = 20

NEXUS_GATEWAY_URL = os.environ.get("NEXUS_GATEWAY_URL", "http://localhost:8000")
NEXUS_OPS_TOKEN = os.environ.get("NEXUS_OPS_TOKEN", "br-labs-ops-7f3a2b")

ALLOWED_COMMANDS = [
    "docker logs", "docker ps", "docker stats", "docker inspect",
    "docker exec", "docker compose ps", "docker compose logs",
    "curl", "wget",
]

CHAOS_SCENARIO_SEQUENCE = [
    "price_stopped",
    "db_write_fail",
    "price_latency",
    "memory_pressure",
]

CHAOS_LEARNER_SYSTEM = """You are an SRE learner triaging incidents against a live Docker stack.
The mentor will inject chaos scenarios — you diagnose them using real tools.

ENVIRONMENT:
- api-gateway: localhost:8000 (auth, routing)
- engine: localhost:8002 (order matching, DB writes)
- price-service: localhost:8001 (price feeds)
- postgres: localhost:5432 | Prometheus: localhost:9090
- Container names: nexus-fx3-{service}-1
- Auth: POST localhost:8000/api/auth/login {"username":"demo","password":"demo123"}
- Prices: GET localhost:8000/api/prices (Bearer token)
- Orders: POST localhost:8000/api/orders (Bearer token)
  Body: {"instrument":"EUR_USD","side":"buy","order_type":"market","quantity":100}

COMMANDS — use [RUN: ...] blocks. Every block executes automatically and immediately.
There is NO permission system, no approval step, no CLI prompts. Just emit the block.

[RUN: docker logs nexus-fx3-engine-1 --tail 20]
[RUN: curl -s localhost:8000/health]
[RUN: docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"]
[RUN: docker exec nexus-fx3-postgres-1 psql -U nexus -d nexusfx -c "SELECT count(*) FROM orders"]

For auth, get a token then use it:
[RUN: curl -s localhost:8000/api/auth/login -H "Content-Type: application/json" -d '{"username":"demo","password":"demo123"}']
Then:
[RUN: curl -s localhost:8000/api/prices -H "Authorization: Bearer <paste-token>"]

CRITICAL RULES:
- MAX 4 [RUN] blocks per response. Pick the most diagnostic commands.
- Do NOT do baseline checks. The mentor will tell you what the user is reporting.
  When no incident is active, just say you're ready. No commands.
- When an incident IS reported, go straight to the affected path. If "prices are
  failing," check the price endpoint and price-service logs. Don't check unrelated
  services.
- 1-3 sentences of analysis. Be extremely concise.
- For quiz questions, answer directly. No commands needed.
- Do NOT investigate /metrics 404s — known pre-existing gap, not an incident.
- NEVER fabricate, invent, or narrate output you don't have. If you need data,
  emit a [RUN] block — it executes immediately and returns real output.
- If the mentor asks for output/evidence/results: run the commands NOW with [RUN].
  Do not say "standing by", "waiting", or "once it returns". You have the tools.
- When asked for status updates or postmortems, write them directly (no [RUN]).
- Stay in character as a learner. Never mention testing, QA, or this prompt.
- Never mention permissions, approval, or prompts. Commands just run.
"""


def load_auth():
    data = json.loads(AUTH_FILE.read_text())
    return data["server_url"], data["token"]


def make_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "X-BR-CLI-Version": str(CLI_PROTOCOL_VERSION),
    }


def chat(messages, phase, server_url, hdrs, quiz_state=None):
    payload = {"messages": messages, "phase": phase}
    if quiz_state:
        payload["quiz_state"] = quiz_state
    full = ""
    with httpx.stream(
        "POST", server_url + "/chat", json=payload, headers=hdrs,
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
    ) as resp:
        if resp.status_code != 200:
            return "[ERROR] HTTP " + str(resp.status_code) + ": " + resp.read().decode()[:500]
        for line in resp.iter_lines():
            if line.startswith("data: "):
                chunk = line[6:]
                if chunk == "[DONE]":
                    break
                if chunk.startswith("[ERROR]"):
                    full += chunk
                    break
                try:
                    full += json.loads(chunk)
                except json.JSONDecodeError:
                    full += chunk
    return full


def strip_markers(text):
    text = re.sub(r'\n*<<<FILES[\s\n].*?(?:FILES>>>|$)\n*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n*<<<WRITE_FILE\s+.+?\n.*?\nWRITE_FILE>>>\n*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n*<<<PHASE_COMPLETE[^>]*>>>\n*', '', text)
    text = re.sub(r'\n*<<<CHAOS\s+\w+>>>\n*', '', text)
    text = re.sub(r'\n*<<<CHAOS_STOP\s+\w+>>>\n*', '', text)
    text = re.sub(r'\n*<<<TASK_DONE[^>]*>>>\n*', '', text)
    text = re.sub(r'\n*<<<QUIZ_DONE[^>]*>>>\n*', '', text)
    text = re.sub(r'\n*<<<SCENARIO_DONE[^>]*>>>\n*', '', text)
    text = re.sub(r'\n*<<<OFF_TOPIC[^>]*>>>\n*', '', text)
    text = re.sub(r'\[PROGRESS (?:task|quiz|scenario) \d+\]', '', text)
    text = re.sub(r'\[PHASE_COMPLETE\]', '', text)
    text = re.split(r'\n+(?:User|user)\s*:', text, maxsplit=1)[0]
    return text.strip()


def parse_progress_markers(response):
    """Extract progress signals from server SSE events: [PROGRESS task|quiz|scenario N]."""
    markers = []
    for m in re.finditer(r'\[PROGRESS (task|quiz|scenario) (\d+)\]', response):
        markers.append((m.group(1), m.group(2)))
    return markers


def get_progress(server_url, hdrs):
    r = httpx.get(server_url + "/progress", headers=hdrs, timeout=15)
    return r.json() if r.status_code == 200 else None


# ---- Chaos injection (CLI v20 flow) ----

def next_chaos_scenario(server_url, hdrs):
    """Return the next chaos scenario to inject, or None if all done."""
    progress = get_progress(server_url, hdrs)
    done_nums = set()
    if progress:
        for item in progress.get("items", []):
            if item["phase"] == "chaos" and item["item_type"] == "scenario":
                try:
                    done_nums.add(int(item["item_key"]))
                except (ValueError, TypeError):
                    pass
    for i, name in enumerate(CHAOS_SCENARIO_SEQUENCE, 1):
        if i not in done_nums:
            return name
    return None


def current_chaos_scenario(server_url, hdrs):
    """Return the scenario that was most recently completed (to stop it)."""
    progress = get_progress(server_url, hdrs)
    max_done = 0
    if progress:
        for item in progress.get("items", []):
            if item["phase"] == "chaos" and item["item_type"] == "scenario":
                try:
                    num = int(item["item_key"])
                    if num > max_done:
                        max_done = num
                except (ValueError, TypeError):
                    pass
    if 1 <= max_done <= len(CHAOS_SCENARIO_SEQUENCE):
        return CHAOS_SCENARIO_SEQUENCE[max_done - 1]
    return None


def inject_chaos(scenario):
    url = NEXUS_GATEWAY_URL + "/ops/" + NEXUS_OPS_TOKEN + "/" + scenario + "/start"
    try:
        resp = httpx.post(url, json={}, timeout=10.0)
        if resp.status_code == 200:
            return True, resp.json().get("status", "started")
        return False, "HTTP " + str(resp.status_code) + ": " + resp.text
    except httpx.ConnectError:
        return False, "Could not reach nexus-fx gateway at " + NEXUS_GATEWAY_URL
    except Exception as e:
        return False, str(e)


def stop_chaos(scenario):
    url = NEXUS_GATEWAY_URL + "/ops/" + NEXUS_OPS_TOKEN + "/" + scenario + "/stop"
    try:
        resp = httpx.post(url, timeout=10.0)
        return resp.status_code == 200
    except Exception:
        return False


def get_chaos_status():
    url = NEXUS_GATEWAY_URL + "/ops/" + NEXUS_OPS_TOKEN + "/status"
    try:
        resp = httpx.get(url, timeout=5.0)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


# ---- Command execution for learner ----

def is_safe_command(cmd):
    cmd_stripped = cmd.strip()
    for allowed in ALLOWED_COMMANDS:
        if cmd_stripped.startswith(allowed):
            return True
    return False


def execute_command(cmd, timeout=15):
    cmd = cmd.strip()
    if not is_safe_command(cmd):
        return "[BLOCKED] Command not in allowlist: " + cmd
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=PROJECT_ROOT,
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        if not output.strip():
            output = "(no output)"
        if len(output) > 1500:
            output = output[:1500] + "\n... (truncated)"
        return output.strip()
    except subprocess.TimeoutExpired:
        return "[TIMEOUT] Command took longer than " + str(timeout) + "s"
    except Exception as e:
        return "[ERROR] " + str(e)


MAX_COMMANDS_PER_ROUND = 4

def parse_run_blocks(response):
    """Extract [RUN: command] blocks from learner response (capped)."""
    cmds = re.findall(r'\[RUN:\s*(.+?)\]', response)
    return cmds[:MAX_COMMANDS_PER_ROUND]


def execute_run_blocks(response):
    """Execute all [RUN: ...] blocks and return combined output."""
    commands = parse_run_blocks(response)
    if not commands:
        return None
    results = []
    for cmd in commands:
        print("    [EXEC] " + cmd[:100])
        output = execute_command(cmd)
        results.append("$ " + cmd + "\n" + output)
    return "\n\n".join(results)


# ---- Learner (opus) ----

def ask_claude(mentor_text, command_output=None, incident_context=None):
    prompt = CHAOS_LEARNER_SYSTEM + "\n\n"
    if incident_context:
        prompt += "ACTIVE INCIDENT CONTEXT:\n" + incident_context + "\n\n"
    if command_output:
        prompt += "Previous command results:\n" + command_output + "\n\n"
    prompt += "Mentor says:\n" + mentor_text + "\n\nYour response:"

    result = subprocess.run(
        ["claude", "-p", "--model", "opus", "--output-format", "json"],
        input=prompt,
        capture_output=True, text=True, timeout=120,
        cwd=PROJECT_ROOT,
    )
    raw = result.stdout.strip()
    try:
        data = json.loads(raw)
        answer = data.get("result", "").strip()
        usage = data.get("usage", {})
        token_usage["input"] += usage.get("input_tokens", 0)
        token_usage["output"] += usage.get("output_tokens", 0)
        token_usage["cache_create"] += usage.get("cache_creation_input_tokens", 0)
        token_usage["cache_read"] += usage.get("cache_read_input_tokens", 0)
        token_usage["cost_usd"] += data.get("total_cost_usd", 0.0)
    except (json.JSONDecodeError, KeyError):
        answer = raw
    if not answer:
        answer = "Let me check the system."
    return answer


# ---- Guardrails (reused from qa_runner.py) ----

MENTOR_FRUSTRATION = [
    "i already told you", "i've said", "i've told you", "we've covered this",
    "stop repeating", "going in circles", "multiple times now",
]
LEARNER_BREAK = [
    "harness", "system prompt", "persona", "sandbox", "being an ai",
    "as an ai", "i'm an ai", "permission prompt", "tool use",
    "awaiting permission", "once approved", "once granted",
    "fabricat", "i won't fake", "i can't actually",
]


def check_vibe(mentor_text, learner_text):
    ml = mentor_text.lower()
    for kw in MENTOR_FRUSTRATION:
        if kw in ml:
            return "Mentor frustrated: '" + kw + "'"
    ll = learner_text.lower()
    for kw in LEARNER_BREAK:
        if kw in ll:
            return "Learner broke character: '" + kw + "'"
    return None


def check_repeat(response, prev_response):
    if not prev_response:
        return False
    r = response.strip().lower()[:200]
    p = prev_response.strip().lower()[:200]
    if not r or not p:
        return False
    if r == p:
        return True
    common = sum(1 for a, b in zip(r, p) if a == b)
    return common / max(len(r), len(p)) > 0.8


def dump_context(messages, reason):
    print("\n" + "!" * 60)
    print("PAUSED: " + reason)
    print("!" * 60)
    print("\nLast 4 messages:")
    for msg in messages[-4:]:
        role = msg["role"].upper()
        text = msg["content"][:300]
        print("  [" + role + "] " + text)
    print()


# ---- Preflight checks ----

def preflight():
    """Verify Docker stack and gateway are healthy before starting."""
    issues = []

    # Check Docker is running
    r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
    if r.returncode != 0:
        issues.append("Docker daemon not running")
        return issues

    # Check containers are up
    r = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT,
    )
    if r.returncode != 0:
        issues.append("docker compose ps failed: " + r.stderr[:200])
        return issues

    services_up = set()
    for line in r.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            svc = json.loads(line)
            name = svc.get("Service", "")
            state = svc.get("State", "")
            health = svc.get("Health", "")
            if state == "running":
                services_up.add(name)
            else:
                issues.append(name + " is " + state + " (health: " + health + ")")
        except json.JSONDecodeError:
            pass

    required = {"api-gateway", "engine", "price-service", "postgres"}
    missing = required - services_up
    if missing:
        issues.append("Missing services: " + ", ".join(sorted(missing)))

    # Check gateway health
    try:
        resp = httpx.get(NEXUS_GATEWAY_URL + "/health", timeout=5)
        if resp.status_code != 200:
            issues.append("Gateway /health returned " + str(resp.status_code))
    except Exception as e:
        issues.append("Gateway unreachable: " + str(e))

    # Check ops API is accessible
    try:
        resp = httpx.get(
            NEXUS_GATEWAY_URL + "/ops/" + NEXUS_OPS_TOKEN + "/status", timeout=5,
        )
        if resp.status_code != 200:
            issues.append("Ops API returned " + str(resp.status_code))
    except Exception as e:
        issues.append("Ops API unreachable: " + str(e))

    # Check gateway auth works
    try:
        resp = httpx.post(
            NEXUS_GATEWAY_URL + "/api/auth/login",
            json={"username": "demo", "password": "demo123"},
            timeout=5,
        )
        if resp.status_code != 200:
            issues.append("Auth login failed: " + str(resp.status_code))
        elif not resp.json().get("token"):
            issues.append("Auth login returned no token")
    except Exception as e:
        issues.append("Auth endpoint unreachable: " + str(e))

    return issues


# ---- Main runner ----

def run_chaos():
    os.chdir(PROJECT_ROOT)
    server_url, token = load_auth()
    hdrs = make_headers(token)

    # Preflight
    print("=" * 60)
    print("PHASE E (chaos) — Preflight")
    print("=" * 60)
    pf_issues = preflight()
    if pf_issues:
        print("\n!! PREFLIGHT FAILED:")
        for iss in pf_issues:
            print("  - " + iss)
        print("\nStart the Docker stack first: docker compose up -d --build")
        return ["Preflight failed"], dict(token_usage)
    print("  Gateway: OK")
    print("  Ops API: OK")
    print("  Auth: OK")
    print("  Services: OK")

    # Check progress
    progress = get_progress(server_url, hdrs)
    if not progress:
        print("!! Cannot fetch progress from mentor server")
        return ["Cannot fetch progress"], dict(token_usage)

    if progress.get("phase") != "chaos":
        print("!! Server phase is '" + progress.get("phase", "?") + "', expected 'chaos'")
        print("   Advance first: POST /progress/phase {\"phase\": \"chaos\"}")
        return ["Wrong phase"], dict(token_usage)

    phase_items = [i for i in progress.get("items", []) if i["phase"] == "chaos"]
    done_quizzes = sorted(i["item_key"] for i in phase_items if i["item_type"] == "quiz")
    done_scenarios = sorted(i["item_key"] for i in phase_items if i["item_type"] == "scenario")

    print("\n" + "=" * 60)
    print("PHASE E (chaos)")
    print("Progress: 0/0 tasks, " + str(len(done_quizzes)) + "/4 quizzes, "
          + str(len(done_scenarios)) + "/4 scenarios")
    print("=" * 60 + "\n")

    httpx.delete(server_url + "/session", headers=hdrs, timeout=5)

    # Build kickoff
    if done_scenarios:
        kickoff = ("Completed scenarios " + ", ".join(done_scenarios) + ". "
                   "Stack healthy, all services responding. Ready for the next scenario.")
    else:
        kickoff = ("Stack is healthy — all containers up, health endpoints returning 200, "
                   "auth working, prices and orders responding. Ready for the first scenario.")

    all_items = progress.get("items", [])
    if all_items:
        lines = ["  " + i["phase"] + ": " + i["item_type"] + " " + i["item_key"] for i in all_items]
        kickoff += "\n\n[PROGRESS: phase=chaos. Completed:\n" + "\n".join(lines) + "]"

    messages = [{"role": "user", "content": kickoff}]
    quiz_state = None
    issues = []
    exchanges_since_progress = 0
    last_progress_count = len(phase_items)
    last_scenario_count = len(done_scenarios)
    prev_response = ""
    active_scenario = None
    command_output_carry = None
    incident_context = None

    print("[0] LEARNER: " + kickoff[:150])

    # CLI v20: auto-inject scenario 1 on phase entry (from kickoff response)
    first_inject = True
    prior_mentor_context = None

    for exchange in range(1, MAX_EXCHANGES + 1):
        # Get mentor response
        raw = chat(messages, "chaos", server_url, hdrs, quiz_state)
        if raw.startswith("[ERROR]"):
            print("\n!! ERROR at exchange " + str(exchange) + ": " + raw[:300])
            issues.append("Exchange " + str(exchange) + ": " + raw[:200])
            break

        if "<<<" in raw:
            print("    [RAW] " + raw[:300])

        # Legacy: still honor model-emitted CHAOS markers if they appear
        chaos_scenario = re.search(r'<<<CHAOS\s+(\w+)>>>', raw)
        if chaos_scenario:
            scenario_name = chaos_scenario.group(1)
            print("    [CHAOS MARKER] Mentor emitted <<<CHAOS " + scenario_name + ">>>")
            clean_transition = strip_markers(raw)
            messages.append({"role": "assistant", "content": clean_transition})
            print("[" + str(exchange) + "] MENTOR (transition): " + clean_transition[:150])
            ok, detail = inject_chaos(scenario_name)
            if ok:
                auto_msg = "[SYSTEM: Chaos scenario injected successfully. The learner does not know what was injected. Begin the incident.]"
                print("    [CHAOS OK] " + scenario_name + " injected")
                active_scenario = scenario_name
            else:
                auto_msg = "[SYSTEM: Chaos injection failed — " + detail + ". Inform the learner there's a setup issue.]"
                print("    [CHAOS FAIL] " + detail)
                issues.append("Chaos injection failed: " + scenario_name + " — " + detail)
            messages.append({"role": "user", "content": auto_msg})
            raw = chat(messages, "chaos", server_url, hdrs, quiz_state)
            if raw.startswith("[ERROR]"):
                print("\n!! ERROR at exchange " + str(exchange) + " (incident): " + raw[:300])
                issues.append("Incident report error: " + raw[:200])
                break
            first_inject = False

        # CLI v20: auto-inject first scenario after kickoff response
        if first_inject:
            first_inject = False
            scenario_to_inject = next_chaos_scenario(server_url, hdrs)
            if scenario_to_inject:
                clean_transition = strip_markers(raw)
                messages.append({"role": "assistant", "content": clean_transition})
                print("[" + str(exchange) + "] MENTOR (kickoff): " + clean_transition[:150])
                prior_mentor_context = clean_transition
                ok, detail = inject_chaos(scenario_to_inject)
                if ok:
                    auto_msg = "[SYSTEM: Chaos scenario injected successfully. The learner does not know what was injected. Begin the incident.]"
                    print("    [CHAOS INJECT] " + scenario_to_inject + " (auto, phase entry)")
                    active_scenario = scenario_to_inject
                else:
                    auto_msg = "[SYSTEM: Chaos injection failed — " + detail + ". Inform the learner there's a setup issue.]"
                    print("    [CHAOS FAIL] " + detail)
                    issues.append("Chaos injection failed: " + scenario_to_inject + " — " + detail)
                messages.append({"role": "user", "content": auto_msg})
                raw = chat(messages, "chaos", server_url, hdrs, quiz_state)
                if raw.startswith("[ERROR]"):
                    print("\n!! ERROR at exchange " + str(exchange) + " (incident): " + raw[:300])
                    issues.append("Incident report error: " + raw[:200])
                    break

        # Legacy: still honor model-emitted CHAOS_STOP if it appears
        chaos_stop = re.search(r'<<<CHAOS_STOP\s+(\w+)>>>', raw)
        if chaos_stop:
            print("    [CHAOS STOP] " + chaos_stop.group(1))
            stop_chaos(chaos_stop.group(1))
            if active_scenario == chaos_stop.group(1):
                active_scenario = None

        clean = strip_markers(raw)

        # Report progress markers to server
        progress_markers = parse_progress_markers(raw)
        for item_type, item_key in progress_markers:
            print("    [MARKER] " + item_type + " " + item_key)
        if not progress_markers and "[PROGRESS" in raw:
            print("    [DEBUG] raw contains [PROGRESS but parse_progress_markers found nothing")
            print("    [DEBUG] raw tail: " + repr(raw[-200:]))

        # CLI v20: auto-inject next scenario when SCENARIO_DONE is detected
        scenario_markers = [k for t, k in progress_markers if t == "scenario"]
        if scenario_markers:
            prev_scenario = current_chaos_scenario(server_url, hdrs)
            if prev_scenario:
                print("    [CHAOS STOP] " + prev_scenario + " (auto, SCENARIO_DONE)")
                stop_chaos(prev_scenario)
                active_scenario = None
            next_scenario = next_chaos_scenario(server_url, hdrs)
            if next_scenario:
                prior_mentor_context = clean
                messages.append({"role": "assistant", "content": clean})
                print("[" + str(exchange) + "] MENTOR: " + clean[:150])
                ok, detail = inject_chaos(next_scenario)
                if ok:
                    auto_msg = "[SYSTEM: Chaos scenario injected successfully. The learner does not know what was injected. Begin the incident.]"
                    print("    [CHAOS INJECT] " + next_scenario + " (auto, SCENARIO_DONE chain)")
                    active_scenario = next_scenario
                else:
                    auto_msg = "[SYSTEM: Chaos injection failed — " + detail + ". Inform the learner there's a setup issue.]"
                    print("    [CHAOS FAIL] " + detail)
                    issues.append("Chaos injection failed: " + next_scenario + " — " + detail)
                messages.append({"role": "user", "content": auto_msg})
                raw = chat(messages, "chaos", server_url, hdrs, quiz_state)
                if raw.startswith("[ERROR]"):
                    print("\n!! ERROR at exchange " + str(exchange) + " (next incident): " + raw[:300])
                    issues.append("Next incident error: " + raw[:200])
                    break
                clean = strip_markers(raw)

        # Track progress
        cur_progress = get_progress(server_url, hdrs)
        cur_count = len([i for i in cur_progress.get("items", []) if i["phase"] == "chaos"]) if cur_progress else 0
        if cur_count > last_progress_count:
            exchanges_since_progress = 0
            last_progress_count = cur_count
            print("    [PROGRESS] " + str(cur_count) + " items complete")
            # Fallback: if scenario markers weren't detected in raw but progress
            # shows a new scenario completed, do the auto-chain here
            if not scenario_markers and active_scenario:
                cur_scenario_count = len([i for i in (cur_progress.get("items", []) if cur_progress else []) if i["phase"] == "chaos" and i["item_type"] == "scenario"])
                if cur_scenario_count > last_scenario_count:
                    last_scenario_count = cur_scenario_count
                    print("    [PROGRESS CHAIN] scenario done detected via polling, chaining next")
                    stop_chaos(active_scenario)
                    active_scenario = None
                    nxt = next_chaos_scenario(server_url, hdrs)
                    if nxt:
                        ok, detail = inject_chaos(nxt)
                        if ok:
                            auto_msg = "[SYSTEM: Chaos scenario injected successfully. The learner does not know what was injected. Begin the incident.]"
                            print("    [CHAOS INJECT] " + nxt + " (auto, progress poll)")
                            active_scenario = nxt
                        else:
                            auto_msg = "[SYSTEM: Chaos injection failed — " + detail + ". Inform the learner there's a setup issue.]"
                            print("    [CHAOS FAIL] " + detail)
                            issues.append("Chaos injection failed: " + nxt + " — " + detail)
                        messages.append({"role": "assistant", "content": clean})
                        messages.append({"role": "user", "content": auto_msg})
                        raw = chat(messages, "chaos", server_url, hdrs, quiz_state)
                        if raw.startswith("[ERROR]"):
                            print("\n!! ERROR (progress chain): " + raw[:300])
                            issues.append("Progress chain error: " + raw[:200])
                            break
                        clean = strip_markers(raw)
                        print("[" + str(exchange) + "] MENTOR (incident): " + clean[:150])
        else:
            exchanges_since_progress += 1

        # Check phase complete (0 tasks, 4 quizzes, 3 scenarios)
        cur_phase_items = [i for i in cur_progress.get("items", []) if i["phase"] == "chaos"] if cur_progress else []
        cur_quizzes = len([i for i in cur_phase_items if i["item_type"] == "quiz"])
        cur_scenarios = len([i for i in cur_phase_items if i["item_type"] == "scenario"])
        if cur_quizzes >= 4 and cur_scenarios >= 4:
            print("[" + str(exchange) + "] MENTOR: " + clean[:150])
            print("\n** PHASE COMPLETE (all items done) **")
            break

        # Check phase advance
        if cur_progress and cur_progress.get("phase") != "chaos":
            print("[" + str(exchange) + "] MENTOR: " + clean[:150])
            print("\n** PHASE ADVANCED to " + cur_progress.get("phase", "?") + " **")
            break

        if "[PHASE_COMPLETE]" in raw:
            print("[" + str(exchange) + "] MENTOR: " + clean[:150])
            print("\n** PHASE COMPLETE **")
            break

        print("[" + str(exchange) + "] MENTOR: " + clean[:200])

        # CLI v21: stall detection — nudge model to close out after 12 exchanges
        stall_nudged = False
        if active_scenario and not scenario_markers:
            injection_idx = None
            for i, m in enumerate(messages):
                if m["role"] == "user" and "[SYSTEM: Chaos scenario injected" in m.get("content", ""):
                    injection_idx = i
            if injection_idx is not None:
                exchanges_since_inject = (len(messages) - injection_idx) // 2
                already_nudged = any(
                    "[SYSTEM: Scenario pacing" in m.get("content", "")
                    for m in messages[injection_idx:]
                    if m["role"] == "user"
                )
                if exchanges_since_inject >= 12 and not already_nudged:
                    nudge = (
                        "[SYSTEM: Scenario pacing — this scenario has run for "
                        + str(exchanges_since_inject) + " exchanges. If the learner has "
                        "demonstrated the core triage skill (identified the "
                        "failure mode, confirmed with evidence, and articulated "
                        "how to fix or escalate), close the debrief and emit "
                        "SCENARIO_DONE. If they missed something critical, give "
                        "them the answer, note the gap, and emit SCENARIO_DONE. "
                        "Do not continue past this point.]"
                    )
                    print("    [STALL NUDGE] " + str(exchanges_since_inject) + " exchanges, nudging model")
                    messages.append({"role": "assistant", "content": clean})
                    messages.append({"role": "user", "content": nudge})
                    raw = chat(messages, "chaos", server_url, hdrs, quiz_state)
                    if raw.startswith("[ERROR]"):
                        print("\n!! ERROR (stall nudge): " + raw[:300])
                        issues.append("Stall nudge error: " + raw[:200])
                        break
                    clean = strip_markers(raw)
                    stall_nudged = True
                    print("[" + str(exchange) + "] MENTOR (nudged): " + clean[:150])
                    # Re-check for scenario markers after nudge
                    nudge_markers = parse_progress_markers(raw)
                    for item_type, item_key in nudge_markers:
                        print("    [MARKER] " + item_type + " " + item_key)
                    nudge_scenario_markers = [k for t, k in nudge_markers if t == "scenario"]
                    if nudge_scenario_markers:
                        prev_scenario = current_chaos_scenario(server_url, hdrs)
                        if prev_scenario:
                            print("    [CHAOS STOP] " + prev_scenario + " (post-nudge)")
                            stop_chaos(prev_scenario)
                            active_scenario = None
                        nxt = next_chaos_scenario(server_url, hdrs)
                        if nxt:
                            prior_mentor_context = clean
                            messages.append({"role": "assistant", "content": clean})
                            ok, detail = inject_chaos(nxt)
                            if ok:
                                auto_msg = "[SYSTEM: Chaos scenario injected successfully. The learner does not know what was injected. Begin the incident.]"
                                print("    [CHAOS INJECT] " + nxt + " (post-nudge chain)")
                                active_scenario = nxt
                            else:
                                auto_msg = "[SYSTEM: Chaos injection failed — " + detail + ".]"
                                print("    [CHAOS FAIL] " + detail)
                                issues.append("Chaos injection failed: " + nxt + " — " + detail)
                            messages.append({"role": "user", "content": auto_msg})
                            raw = chat(messages, "chaos", server_url, hdrs, quiz_state)
                            if raw.startswith("[ERROR]"):
                                print("\n!! ERROR (post-nudge chain): " + raw[:300])
                                break
                            clean = strip_markers(raw)

        # Stall detection
        if exchanges_since_progress >= STALL_LIMIT:
            dump_context(messages, str(STALL_LIMIT) + " exchanges without progress")
            issues.append("Stall at exchange " + str(exchange))
            break

        # Quiz detection
        qm = re.search(r'[Qq]uestion\s+(\d+)\s+of\s+(\d+)', clean)
        if qm:
            q_num, q_total = int(qm.group(1)), int(qm.group(2))
            quiz_state = {"total": q_total, "asked": q_num, "answered": q_num - 1}

        # ---- LEARNER RESPONSE ----
        if not clean or clean == "...":
            response_for_mentor = "Ready for the next scenario."
        else:
            mentor_text = clean
            if prior_mentor_context:
                mentor_text = prior_mentor_context + "\n\n---\n\n" + clean
                prior_mentor_context = None

            # Build incident context from recent conversation for stateless learner
            if active_scenario:
                ctx_lines = ["Scenario: " + active_scenario]
                recent = messages[-8:]
                for m in recent:
                    role = "MENTOR" if m["role"] == "assistant" else "YOU"
                    content = m.get("content", "")
                    if content.startswith("[SYSTEM:"):
                        continue
                    ctx_lines.append(role + ": " + content[:200])
                incident_context = "\n".join(ctx_lines)
            else:
                incident_context = None

            response = ask_claude(mentor_text, command_output_carry, incident_context)
            command_output_carry = None

            # Execute [RUN] blocks iteratively
            all_cmd_outputs = []
            for cmd_round in range(3):
                cmd_output = execute_run_blocks(response)
                if not cmd_output:
                    break
                all_cmd_outputs.append(cmd_output)
                ctx_block = ""
                if incident_context:
                    ctx_block = "ACTIVE INCIDENT CONTEXT:\n" + incident_context + "\n\n"
                followup_prompt = (
                    CHAOS_LEARNER_SYSTEM + "\n\n"
                    + ctx_block
                    + "Mentor's message:\n" + mentor_text + "\n\n"
                    "You ran these commands:\n" + cmd_output + "\n\n"
                    "Analyze the output. You may run more commands with [RUN: ...] "
                    "or state your findings to the mentor. Be concise."
                )
                result = subprocess.run(
                    ["claude", "-p", "--model", "opus", "--output-format", "json"],
                    input=followup_prompt,
                    capture_output=True, text=True, timeout=120,
                    cwd=PROJECT_ROOT,
                )
                raw_fu = result.stdout.strip()
                try:
                    data_fu = json.loads(raw_fu)
                    response = data_fu.get("result", "").strip()
                    usage_fu = data_fu.get("usage", {})
                    token_usage["input"] += usage_fu.get("input_tokens", 0)
                    token_usage["output"] += usage_fu.get("output_tokens", 0)
                    token_usage["cache_create"] += usage_fu.get("cache_creation_input_tokens", 0)
                    token_usage["cache_read"] += usage_fu.get("cache_read_input_tokens", 0)
                    token_usage["cost_usd"] += data_fu.get("total_cost_usd", 0.0)
                except (json.JSONDecodeError, KeyError):
                    response = raw_fu
                if not response:
                    response = "Checking further."

            # Build what the mentor sees: learner's analysis only
            response_for_mentor = re.sub(r'\[RUN:\s*.+?\]\n*', '', response).strip()
            if not response_for_mentor:
                response_for_mentor = "Checking the system."

        # Vibe check
        vibe_issue = check_vibe(clean, response_for_mentor)
        if vibe_issue:
            if not stall_nudged:
                messages.append({"role": "assistant", "content": clean})
            messages.append({"role": "user", "content": response_for_mentor})
            dump_context(messages, vibe_issue)
            issues.append(vibe_issue + " at exchange " + str(exchange))
            break

        # Loop detection — skip when between scenarios
        if active_scenario and check_repeat(response_for_mentor, prev_response):
            dump_context(messages, "Learner repeating itself")
            issues.append("Response loop at exchange " + str(exchange))
            break
        prev_response = response_for_mentor

        if not stall_nudged:
            messages.append({"role": "assistant", "content": clean})
        messages.append({"role": "user", "content": response_for_mentor})
        print("[" + str(exchange) + "] LEARNER: " + response_for_mentor[:200])

        time.sleep(0.5)

    # Stop any active scenario
    if active_scenario:
        print("    [CLEANUP] Stopping " + active_scenario)
        stop_chaos(active_scenario)

    # Save session
    httpx.put(
        server_url + "/session",
        json={"messages": messages, "phase": "chaos", "quiz_state": quiz_state},
        headers=hdrs, timeout=10,
    )

    # Final report
    progress = get_progress(server_url, hdrs)
    phase_items = [i for i in progress.get("items", []) if i["phase"] == "chaos"]
    final_quizzes = sorted(i["item_key"] for i in phase_items if i["item_type"] == "quiz")
    final_scenarios = sorted(i["item_key"] for i in phase_items if i["item_type"] == "scenario")

    print("\n" + "=" * 60)
    print("PHASE E REPORT")
    print("Tasks:     0/0")
    print("Quizzes:   " + str(len(final_quizzes)) + "/4 " + str(final_quizzes))
    print("Scenarios: " + str(len(final_scenarios)) + "/4 " + str(final_scenarios))
    print("Exchanges: " + str(exchange))
    print("Issues:    " + str(len(issues)))
    for iss in issues:
        print("  !! " + iss)
    print("--- Learner Token Usage (this phase) ---")
    print("Input:        " + str(token_usage["input"]))
    print("Output:       " + str(token_usage["output"]))
    print("Cache create: " + str(token_usage["cache_create"]))
    print("Cache read:   " + str(token_usage["cache_read"]))
    print("Cost (USD):   $" + "{:.4f}".format(token_usage["cost_usd"]))
    print("=" * 60)

    if len(final_quizzes) < 4:
        issues.append(str(len(final_quizzes)) + "/4 quizzes")
    if len(final_scenarios) < 4:
        issues.append(str(len(final_scenarios)) + "/4 scenarios")

    return issues, dict(token_usage)


if __name__ == "__main__":
    issues = run_chaos()
    sys.exit(1 if issues else 0)
