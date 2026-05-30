# Docker Concepts Reference

## Volumes

Docker Compose uses two volume mechanisms:

### Named Volumes

Declared at the top level of `docker-compose.yml`:

```yaml
volumes:
  pgdata:
```

A named volume is managed by Docker. It lives outside any container in Docker's own storage area. It survives `docker-compose down` — your data persists between restarts. You need `docker-compose down -v` (the `-v` flag) or `docker volume rm` to delete it.

Attached to a container path via:

```yaml
- pgdata:/var/lib/postgresql/data
```

This mounts the named volume to where Postgres stores its data files. Every time the container starts, it sees the same data directory.

### Bind Mounts

Maps a file or directory on the host directly into the container:

```yaml
- ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
```

Left side = local path, right side = path inside the container. Changes on the host are immediately visible inside the container and vice versa. The `initdb.d` directory is a Postgres convention — it runs any `.sql` files there on first database creation only.

### Key Distinction

Named volumes are Docker-managed and portable. Bind mounts are direct filesystem mappings — great for development (live code reloading, config injection) but tied to the host's file paths.

### Fargate Implications

Named volumes don't exist on Fargate. AWS EFS is Fargate's equivalent — a managed filesystem that can attach to ephemeral Fargate tasks. Bind mounts for config files (like `init.sql`) get baked into the Docker image instead (copied in the Dockerfile), since there's no host filesystem on Fargate.

---

## depends_on

Controls container startup order. Two forms:

### Simple Form (order only)

Waits for the container to *start*, not to be ready:

```yaml
depends_on:
  - postgres
```

### Condition Form (order + health)

Waits for the healthcheck to pass before starting the dependent:

```yaml
depends_on:
  postgres:
    condition: service_healthy
```

### Startup Chain in Nexus

1. **postgres** starts first, runs `pg_isready` every 5s
2. **price-service** waits until postgres is healthy, then starts
3. **engine** waits until both postgres and price-service are healthy
4. **api-gateway** waits until both engine and price-service are healthy

### Runtime Limitation

`depends_on` only matters at startup. If postgres crashes later while engine is running, Docker won't stop engine. Runtime resilience is handled via restart policies or orchestrator-level health checks.

---

## Healthcheck

A healthcheck is just a command that Docker runs inside the container and checks the exit code. Exit 0 = healthy, anything else = unhealthy. Docker doesn't know or care what the command does.

### Configuration

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U nexus"]
  interval: 5s
  timeout: 3s
  retries: 5
```

- **test** — the command to run. `CMD-SHELL` wraps it in `sh -c` (gives you pipes, `&&`). `CMD` runs the binary directly.
- **interval** — how often Docker runs the test
- **timeout** — how long to wait before counting it as a failure
- **retries** — consecutive failures before marking `unhealthy`

### Container Lifecycle

```
starting → (first test passes) → healthy
healthy  → (retries consecutive failures) → unhealthy
```

### Timing Math

Postgres: 5 failures x 5s interval = 25 seconds max before unhealthy.
Python services: 3 failures x 10s interval = 30 seconds max.

### Important Note

Docker's healthcheck only **reports** status. It doesn't restart or kill anything. The `depends_on: condition: service_healthy` in other services *acts* on that status. For auto-restart on unhealthy, add `restart: on-failure` or `restart: unless-stopped` to the service definition.

---

## Containers and Services

Each `services:` entry in `docker-compose.yml` becomes its own container — its own filesystem, network interface, and process space. Nexus runs four containers: postgres, price-service, engine, api-gateway.

### Isolation

Containers can't see each other's filesystems or processes. Communication is over the network only. Docker Compose creates a shared network automatically, and each container is reachable by its service name as a hostname:

```yaml
PRICE_SERVICE_URL: http://price-service:8001
POSTGRES_HOST: postgres
```

Docker's internal DNS resolves `price-service` to that container's IP. From engine's perspective, `localhost` is itself — postgres is a different machine on the network.

### Image to Container

One Dockerfile produces one image. One image can produce multiple identical containers — that's horizontal scaling. Each is an independent copy of the same process.

The "one container, one process" convention exists because healthcheck, logging, and restart behavior all assume a single main process. Multiple processes in one container make it ambiguous what "healthy" or "restart" means.

### Sidecars

Multiple containers can share a network namespace within a single ECS Task (not in basic Docker Compose). Common patterns: app + log shipper, app + metrics collector, app + envoy proxy. Each sidecar is still one process, one image, one purpose.

---

## Rebuilding

Static files and application code are copied into images at build time via `COPY . .` in the Dockerfile. Editing files on your host doesn't affect running containers. To pick up changes:

```bash
# Rebuild and restart one service (others keep running)
docker-compose up --build -d api-gateway

# Rebuild everything
docker-compose up --build

# Nuclear option: wipe volumes and rebuild
docker-compose down -v
docker-compose up --build
```

The `-v` flag on `down` deletes named volumes, which forces Postgres to re-initialize from `init.sql`.
