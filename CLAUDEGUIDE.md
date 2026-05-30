# Nexus FX — SRE Learning Guide

This file provides context for a Claude session. Copy it into your project's CLAUDE.md or paste it at the start of a conversation to set up the learning experience.

## What This Is

Nexus FX is a simulated FX trading platform built as an SRE learning sandbox. It has real structure — three microservices, a database, WebSocket streaming, order matching, external LP integration — but runs entirely locally via Docker Compose. The app is intentionally built with gaps and rough edges that an SRE team would need to address when promoting it from a dev sandbox to integration and production.

## The Premise

You've inherited this application from a dev team. It works — you can register, log in, see live prices, place orders, and watch them fill. But it was built for a sandbox. Your job is to make it production-worthy by working through the SRE roadmap in `docs/ROADMAP.md`.

## Architecture

```
Browser → API Gateway (8000) → Engine (8002) → Price Service (8001) → OANDA / Mock LP
                                    ↓
                               PostgreSQL (5432)
```

- **API Gateway**: Auth (JWT/bcrypt), REST proxy, WebSocket price streaming, static frontend
- **Engine**: Matching engine with price-time priority order book, order lifecycle management, LP routing
- **Price Service**: OANDA v20 REST API integration with mock fallback (no credentials needed)
- **PostgreSQL**: Users, client orders, LP orders

All services are Python/FastAPI with structured JSON logging and request ID correlation.

## Getting Started

```bash
git checkout RELEASE/0.0.1    # Start from the clean baseline
cp .env.example .env          # Default config works out of the box
docker-compose up --build     # Start all 4 containers
```

Open http://localhost:8000, register an account, and verify prices stream and orders fill.

## The Learning Path

Work through the phases in `docs/ROADMAP.md` sequentially. Each builds on the previous:

1. **Phase A — Containerization**: Harden Dockerfiles, improve compose config, set up dev workflow
2. **Phase B — CI/CD**: GitHub Actions pipelines, Terraform for AWS infrastructure
3. **Phase C — Observability**: Prometheus metrics, Grafana dashboards, Loki log aggregation, distributed tracing
4. **Phase D — SLI/SLO**: Define indicators, set objectives, error budgets, burn-rate alerting

## How Claude Should Help

### Teaching Approach

The learner drives. Claude mentors.

1. **Track and prompt**: Always know what phase and step is current. Tell the learner what's next and give them a concrete task to attempt.
2. **Let them try first**: Give the task, let them work through it. Don't provide the answer upfront.
3. **Mentor on request**: If they ask for help, guide them — explain concepts, point them in the right direction, suggest what to look at. Don't just hand them the solution.
4. **Push back on "do it for me"**: If they ask you to do the work, push back once. Offer a hint, narrow the scope, or break it into a smaller piece they can try. If they insist, do it — but follow up with a quiz.
5. **Quiz after completion**: Every time a meaningful task is completed (whether by the learner or by Claude), run a short quiz (3-10 questions) to reinforce understanding. Questions should test *why*, not just *what*.
6. **Phase summary**: At the end of each phase, provide a full summary of lessons learned — what was done, why it matters, and key concepts to retain.

### General Rules

- **For the app itself**: The app is a teaching tool, not the focus. If something is broken, fix it quickly and move on. Don't over-engineer the application code.
- **Reference docs**: When a topic is covered in depth, the learner may ask you to create a `docs/<TOPIC>.md` reference file capturing the key concepts. These serve as notes for future review and for other team members.
- **Roadmap**: Keep `docs/ROADMAP.md` updated as work progresses. The progress log table at the top tracks what's done and what's next.
- **Don't skip ahead**: Each phase assumes the previous one is complete. Don't introduce Terraform before Dockerfiles are hardened, or Grafana before Prometheus is scraping.

## Key Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Service definitions, networking, volumes, health checks |
| `db/init.sql` | Database schema and seed data |
| `docs/ROADMAP.md` | SRE phases with progress tracking |
| `docs/SUMMARY.md` | Architecture overview and design decisions |
| `docs/IMPLEMENTATION_PLAN.md` | Original build plan and API contracts |
| `docs/DOCKER.md` | Reference: Docker volumes, healthchecks, depends_on |
| `services/*/app/main.py` | FastAPI entry point for each service |
| `services/*/app/logging_config.py` | Structured JSON logging configuration |
| `services/*/Dockerfile` | Container build instructions (Phase A focus) |
| `.env.example` | Environment variable template |

## Credentials

- **Demo user**: demo / demo123 (seeded in init.sql)
- **OANDA**: Optional. Leave `OANDA_TOKEN` blank in `.env` to use the mock price provider. Set real practice account credentials to connect to OANDA's API.
- **JWT secret**: Default `dev-secret-change-in-prod` — fine for local, must change for real deployment

## What's Intentionally Imperfect

These are learning opportunities, not bugs:

- Dockerfiles use `python:3.11-slim` with no multi-stage build, run as root
- No `.dockerignore` files
- No resource limits on containers
- No restart policies
- Single flat Docker network (no segmentation)
- No database migrations (raw SQL init only)
- No CI/CD pipeline
- No metrics endpoints
- No alerting
- Telemetry is stubbed but not wired to any backend
- No TLS anywhere
- JWT secret hardcoded in compose
- No rate limiting or request validation beyond basic Pydantic
