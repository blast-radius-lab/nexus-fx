#!/usr/bin/env bash
#
# Set up the local Nexus FX environment.
# Run from the project root: ./scripts/setup.sh
#
# Installs prerequisites, creates the database, configures .env,
# sets up a virtual environment, and installs dependencies.
# Services must be started manually (3 separate terminals).

set -euo pipefail

pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; }
info() { printf "  \033[36m→\033[0m %s\n" "$1"; }
header() { printf "\n\033[1m%s\033[0m\n" "$1"; }
ask() {
    printf "  \033[33m?\033[0m %s [Y/n] " "$1"
    read -r answer
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

ERRORS=0

# ── Must be run from project root ──
if [ ! -f "db/init.sql" ] || [ ! -d "services" ]; then
    fail "Run this from the nexus-fx project root: ./scripts/setup.sh"
    exit 1
fi

# ── Python ──
header "Python 3.13+"
if command -v python3 &>/dev/null; then
    py_version=$(python3 --version 2>&1 | awk '{print $2}')
    py_major=$(echo "$py_version" | cut -d. -f1)
    py_minor=$(echo "$py_version" | cut -d. -f2)
    if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 13 ]; then
        pass "Python $py_version"
    else
        fail "Python $py_version found, need 3.13+"
        if command -v brew &>/dev/null; then
            if ask "Install Python 3.13 via Homebrew?"; then
                brew install python@3.13
                pass "Python 3.13 installed — you may need to restart your terminal"
            else
                info "Install manually: brew install python@3.13"
                info "  or use pyenv: brew install pyenv && pyenv install 3.13.7"
                ERRORS=$((ERRORS + 1))
            fi
        else
            info "Install pyenv: https://github.com/pyenv/pyenv"
            info "  then: pyenv install 3.13.7 && pyenv global 3.13.7"
            ERRORS=$((ERRORS + 1))
        fi
    fi
else
    fail "python3 not found"
    info "Install: brew install python@3.13"
    info "  or use pyenv: brew install pyenv && pyenv install 3.13.7"
    ERRORS=$((ERRORS + 1))
fi

# ── PostgreSQL ──
header "PostgreSQL"

# Detect the installed brew formula name (postgresql@16, postgresql@15, etc.)
pg_formula=""
if command -v brew &>/dev/null; then
    pg_formula=$(brew list 2>/dev/null | grep -E '^postgresql(@[0-9]+)?$' | head -1)
fi

if command -v pg_isready &>/dev/null; then
    pass "PostgreSQL is installed${pg_formula:+ ($pg_formula)}"
    if pg_isready -q 2>/dev/null; then
        pass "PostgreSQL is running"
    else
        info "Starting PostgreSQL..."
        started=false
        if [ -n "$pg_formula" ]; then
            brew services start "$pg_formula" 2>/dev/null && started=true
        elif command -v brew &>/dev/null; then
            brew services start postgresql 2>/dev/null && started=true
        fi
        if [ "$started" = true ]; then
            sleep 2
        fi
        if pg_isready -q 2>/dev/null; then
            pass "PostgreSQL started"
        else
            fail "Could not start PostgreSQL"
            info "Try: brew services restart ${pg_formula:-postgresql}"
            ERRORS=$((ERRORS + 1))
        fi
    fi
else
    fail "PostgreSQL not installed"
    if command -v brew &>/dev/null; then
        if ask "Install PostgreSQL 16 via Homebrew?"; then
            brew install postgresql@16
            brew services start postgresql@16
            sleep 2
            if pg_isready -q 2>/dev/null; then
                pass "PostgreSQL installed and running"
            else
                fail "Installed but not responding — try: brew services restart postgresql@16"
                ERRORS=$((ERRORS + 1))
            fi
        else
            info "Install manually: brew install postgresql@16"
            ERRORS=$((ERRORS + 1))
        fi
    else
        info "Install PostgreSQL 16: https://www.postgresql.org/download/"
        ERRORS=$((ERRORS + 1))
    fi
fi

# ── Database ──
header "Database"
if command -v psql &>/dev/null && pg_isready -q 2>/dev/null; then
    # Check if user exists
    if psql -U nexus -d postgres -c "SELECT 1" &>/dev/null; then
        pass "User 'nexus' exists"
    else
        info "Creating database user 'nexus'..."
        createuser -s nexus 2>/dev/null || createuser nexus 2>/dev/null || true
        # Set password
        psql -d postgres -c "ALTER USER nexus WITH PASSWORD 'nexus_dev';" &>/dev/null || true
        if psql -U nexus -d postgres -c "SELECT 1" &>/dev/null; then
            pass "User 'nexus' created"
        else
            fail "Could not create user 'nexus'"
            info "Run manually: createuser -P nexus  (password: nexus_dev)"
            ERRORS=$((ERRORS + 1))
        fi
    fi

    # Check if database exists
    if psql -U nexus -d nexus -c "SELECT 1" &>/dev/null; then
        pass "Database 'nexus' exists"
    else
        info "Creating database 'nexus'..."
        createdb -O nexus nexus 2>/dev/null || true
        if psql -U nexus -d nexus -c "SELECT 1" &>/dev/null; then
            pass "Database 'nexus' created"
        else
            fail "Could not create database 'nexus'"
            info "Run manually: createdb -O nexus nexus"
            ERRORS=$((ERRORS + 1))
        fi
    fi

    # Check tables
    if psql -U nexus -d nexus -c "SELECT 1" &>/dev/null; then
        table_count=$(psql -U nexus -d nexus -tAc \
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','client_orders','lp_orders')" \
            2>/dev/null || echo "0")
        if [ "$table_count" -eq 3 ]; then
            pass "Tables exist (users, client_orders, lp_orders)"
        else
            info "Running db/init.sql..."
            if psql -U nexus -d nexus -f db/init.sql &>/dev/null; then
                pass "Tables created and demo user seeded"
            else
                fail "init.sql failed"
                info "Run manually: psql -U nexus -d nexus -f db/init.sql"
                ERRORS=$((ERRORS + 1))
            fi
        fi
    fi
else
    info "Skipping database setup — PostgreSQL not available"
fi

# ── Environment file ──
header "Environment"
if [ -f ".env" ]; then
    pass ".env file exists"
else
    if [ -f ".env.example" ]; then
        info "Creating .env from .env.example..."
        cp .env.example .env
        pass ".env created"
    else
        fail ".env.example not found — can't create .env"
        ERRORS=$((ERRORS + 1))
    fi
fi

if [ -f ".env" ]; then
    changed=0
    # Fix POSTGRES_HOST
    if grep -q "POSTGRES_HOST=postgres" .env 2>/dev/null && ! grep -q "POSTGRES_HOST=localhost" .env 2>/dev/null; then
        sed -i '' 's/POSTGRES_HOST=postgres/POSTGRES_HOST=localhost/' .env 2>/dev/null || \
        sed -i 's/POSTGRES_HOST=postgres/POSTGRES_HOST=localhost/' .env 2>/dev/null
        changed=1
    fi
    # Fix PRICE_SERVICE_URL
    if grep -q "PRICE_SERVICE_URL=http://price-service" .env 2>/dev/null; then
        sed -i '' 's|PRICE_SERVICE_URL=http://price-service:8001|PRICE_SERVICE_URL=http://localhost:8001|' .env 2>/dev/null || \
        sed -i 's|PRICE_SERVICE_URL=http://price-service:8001|PRICE_SERVICE_URL=http://localhost:8001|' .env 2>/dev/null
        changed=1
    fi
    # Fix ENGINE_SERVICE_URL
    if grep -q "ENGINE_SERVICE_URL=http://engine" .env 2>/dev/null; then
        sed -i '' 's|ENGINE_SERVICE_URL=http://engine:8002|ENGINE_SERVICE_URL=http://localhost:8002|' .env 2>/dev/null || \
        sed -i 's|ENGINE_SERVICE_URL=http://engine:8002|ENGINE_SERVICE_URL=http://localhost:8002|' .env 2>/dev/null
        changed=1
    fi
    if [ "$changed" -eq 1 ]; then
        pass "Updated .env with localhost values"
    else
        pass ".env already configured for local dev"
    fi
fi

# ── Virtual environment ──
header "Virtual environment"
if [ -d ".venv" ]; then
    pass ".venv directory exists"
else
    info "Creating virtual environment..."
    python3 -m venv .venv
    pass ".venv created"
fi

# Activate it for dependency installation
# shellcheck disable=SC1091
source .venv/bin/activate
pass "Activated .venv"

# ── Dependencies ──
header "Dependencies"
for svc in price-service engine api-gateway; do
    req="services/$svc/requirements.txt"
    if [ ! -f "$req" ]; then
        fail "$svc: requirements.txt not found"
        ERRORS=$((ERRORS + 1))
        continue
    fi
    # Check a key package from each service
    case "$svc" in
        price-service) check_pkg="fastapi" ;;
        engine)        check_pkg="asyncpg" ;;
        api-gateway)   check_pkg="bcrypt" ;;
    esac
    if python3 -c "import $check_pkg" 2>/dev/null; then
        pass "$svc — already installed"
    else
        info "Installing $svc dependencies..."
        if pip install -q -r "$req" 2>/dev/null; then
            pass "$svc — installed"
        else
            fail "$svc — pip install failed"
            info "Run manually: pip install -r $req"
            ERRORS=$((ERRORS + 1))
        fi
    fi
done

# ── Service health checks ──
header "Services"
all_healthy=true
for port_svc in "8001:price-service" "8002:engine" "8000:api-gateway"; do
    port="${port_svc%%:*}"
    svc="${port_svc##*:}"
    resp=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port/health" 2>/dev/null || echo "000")
    if [ "$resp" = "200" ]; then
        pass "$svc (port $port) — healthy"
    else
        info "$svc (port $port) — not running"
        all_healthy=false
    fi
done

# ── Summary ──
printf "\n\033[1m─────────────────────────────────────────\033[0m\n"
if [ "$ERRORS" -gt 0 ]; then
    printf "\n\033[31m  %d issues need manual attention (see above).\033[0m\n\n" "$ERRORS"
    exit 1
elif [ "$all_healthy" = false ]; then
    printf "\n\033[32m  Environment is ready!\033[0m\n"
    printf "  Start each service in its own terminal:\n\n"
    printf "    \033[36mTerminal 1:\033[0m cd services/price-service && source ../../.venv/bin/activate && python -m uvicorn app.main:app --port 8001\n"
    printf "    \033[36mTerminal 2:\033[0m cd services/engine && source ../../.venv/bin/activate && python -m uvicorn app.main:app --port 8002\n"
    printf "    \033[36mTerminal 3:\033[0m cd services/api-gateway && source ../../.venv/bin/activate && python -m uvicorn app.main:app --port 8000\n"
    printf "\n  Then re-run \033[1m./scripts/setup.sh\033[0m to confirm all health checks pass.\n\n"
    exit 0
else
    printf "\n\033[32m  All checks passed! Run: br-mentor chat\033[0m\n\n"
    exit 0
fi
