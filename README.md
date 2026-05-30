# Nexus FX

A simulated FX trading platform built as an SRE learning sandbox. Three FastAPI microservices handle authentication, market data, and order execution against a synthetic price feed.

## Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │                  Client                     │
                        │           (browser / curl / ws)             │
                        └────────────┬──────────────┬─────────────────┘
                              HTTP   │              │  WebSocket
                                     ▼              ▼
                        ┌─────────────────────────────────────────────┐
                        │          api-gateway :8000                   │
                        │                                             │
                        │  /api/auth/*    ── JWT issue + verify       │
                        │  /api/prices/*  ── proxy to price-service   │
                        │  /api/orders/*  ── proxy to engine          │
                        │  /api/trades/*  ── proxy to engine          │
                        │  /api/account/* ── proxy to engine          │
                        │  /ws/prices     ── real-time price stream   │
                        └──────┬──────────────────────┬───────────────┘
                               │                      │
                  ┌────────────▼──────┐    ┌──────────▼───────────┐
                  │ price-service:8001│    │    engine :8002       │
                  │                   │    │                       │
                  │ Mock price feed   │    │ Order book (limit)    │
                  │ LP execution      │◄───│ Matching engine       │
                  │ Candle generation │    │ LP routing            │
                  └───────────────────┘    └───────────┬───────────┘
                                                       │
                                              ┌────────▼────────┐
                                              │ PostgreSQL :5432 │
                                              │                  │
                                              │ users            │
                                              │ client_orders    │
                                              │ lp_orders        │
                                              └─────────────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| **api-gateway** | 8000 | Public entry point. Handles JWT auth, proxies all API calls, serves WebSocket price stream |
| **price-service** | 8001 | Synthetic market data (9 FX pairs), LP order execution, candle generation |
| **engine** | 8002 | Order matching, limit order book, LP routing, trade lifecycle management |
| **postgres** | 5432 | User accounts, order history, LP fill records |

## Supported Instruments

EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, NZD/USD, USD/CAD, EUR/GBP, EUR/JPY

## Workflows

### Authentication

```
  Client                    api-gateway                    PostgreSQL
    │                           │                              │
    │  POST /api/auth/register  │                              │
    │  {username, password}     │                              │
    │ ─────────────────────────►│                              │
    │                           │  INSERT user                 │
    │                           │  (bcrypt hashed password)    │
    │                           │ ────────────────────────────►│
    │                           │                              │
    │                           │◄─────────────────────────────│
    │                           │                              │
    │                           │  Generate JWT (HS256)        │
    │                           │  payload: {user_id,          │
    │                           │    username, exp, iat}       │
    │  {token, user_id}        │                              │
    │ ◄────────────────────────│                              │
    │                           │                              │
    │                           │                              │
    │  POST /api/auth/login     │                              │
    │  {username, password}     │                              │
    │ ─────────────────────────►│                              │
    │                           │  SELECT user                 │
    │                           │  verify bcrypt hash          │
    │                           │ ────────────────────────────►│
    │                           │                              │
    │                           │◄─────────────────────────────│
    │                           │                              │
    │  {token, user_id}        │  Generate JWT                │
    │ ◄────────────────────────│                              │
    │                           │                              │
    │                           │                              │
    │  GET /api/* (any)         │                              │
    │  Authorization: Bearer <t>│                              │
    │ ─────────────────────────►│                              │
    │                           │  Decode JWT                  │
    │                           │  Verify signature + expiry   │
    │                           │  Extract user_id, username   │
    │                           │                              │
    │                           │  (proceed to route handler)  │
```

All API routes except `/health`, `/api/auth/register`, and `/api/auth/login` require a valid JWT in the `Authorization: Bearer` header. WebSocket connections pass the token as a `?token=` query parameter.

**Default credentials:** `demo` / `demo123` (seeded with $100,000 balance)

### Pricing

```
  Client              api-gateway           price-service              MockProvider
    │                      │                      │                         │
    │ GET /api/prices      │                      │                         │
    │ ?instruments=EUR_USD │                      │                         │
    │ ────────────────────►│                      │                         │
    │                      │ GET /prices/current   │                         │
    │                      │ ─────────────────────►│                         │
    │                      │                       │  Read from PriceCache   │
    │                      │                       │  (in-memory dict)       │
    │                      │ {EUR_USD: {bid, ask}} │                         │
    │                      │ ◄─────────────────────│                         │
    │ {prices: {...}}      │                       │                         │
    │ ◄───────────────────│                       │                         │
    │                      │                       │                         │
    │                      │                       │                         │
    │                      │         Background (every 1.5s):               │
    │                      │                       │  poll provider          │
    │                      │                       │ ────────────────────────►
    │                      │                       │                         │
    │                      │                       │                         │ Gaussian random
    │                      │                       │                         │ walk on 9 pairs
    │                      │                       │                         │ (clamped +/-0.5%)
    │                      │                       │  {prices}               │
    │                      │                       │ ◄────────────────────────
    │                      │                       │  Update cache           │
    │                      │                       │                         │


  WebSocket flow:

  Client              api-gateway           price-service
    │                      │                      │
    │ WS /ws/prices        │                      │
    │ ?token=<jwt>         │                      │
    │ ════════════════════►│                      │
    │                      │ Verify JWT            │
    │                      │                      │
    │                      │     Every 1.5s:       │
    │                      │ GET /prices/current   │
    │                      │ ─────────────────────►│
    │                      │ ◄─────────────────────│
    │  {prices: {...}}     │                      │
    │ ◄═══════════════════│                      │
    │                      │     (repeat)          │
```

The MockProvider generates realistic price movement using a Gaussian random walk seeded from base prices for each pair. Spreads are configured per instrument (e.g., EUR/USD: 1.5 pips, USD/JPY: 1.5 pips). Candle data is fully synthetic, generated on request by walking prices backward from the current value.

### Trading

```
  Client           api-gateway          engine              price-service       PostgreSQL
    │                   │                  │                      │                 │
    │ POST /api/orders  │                  │                      │                 │
    │ {instrument,      │                  │                      │                 │
    │  side: "BUY",     │                  │                      │                 │
    │  order_type,      │                  │                      │                 │
    │  quantity}        │                  │                      │                 │
    │ ─────────────────►│                  │                      │                 │
    │                   │ POST /orders/    │                      │                 │
    │                   │ submit           │                      │                 │
    │                   │ ────────────────►│                      │                 │
    │                   │                  │                      │                 │


  MARKET ORDER:         │                  │                      │                 │
                        │                  │ INSERT client_order   │                 │
                        │                  │ (status: PENDING)     │                 │
                        │                  │ ──────────────────────┼────────────────►│
                        │                  │                       │                 │
                        │                  │ GET /prices/current   │                 │
                        │                  │ ─────────────────────►│                 │
                        │                  │ {bid, ask}            │                 │
                        │                  │ ◄─────────────────────│                 │
                        │                  │                       │                 │
                        │                  │ match_price =         │                 │
                        │                  │   ask (BUY)           │                 │
                        │                  │   bid (SELL)          │                 │
                        │                  │                       │                 │
                        │                  │ UPDATE status=MATCHED │                 │
                        │                  │ ──────────────────────┼────────────────►│
                        │                  │                       │                 │
                        │                  │ POST /lp/execute      │                 │
                        │                  │ {instrument, side,    │                 │
                        │                  │  units, price}        │                 │
                        │                  │ ─────────────────────►│                 │
                        │                  │                       │ (MockProvider   │
                        │                  │                       │  always fills)  │
                        │                  │ {fill_price, order_id}│                 │
                        │                  │ ◄─────────────────────│                 │
                        │                  │                       │                 │
                        │                  │ INSERT lp_order       │                 │
                        │                  │ UPDATE status=FILLED  │                 │
                        │                  │ ──────────────────────┼────────────────►│
                        │                  │                       │                 │
    │ {order_id,        │                  │                       │                 │
    │  status: FILLED,  │                  │                       │                 │
    │  fill_price}      │                  │                       │                 │
    │ ◄────────────────│◄─────────────────│                       │                 │


  LIMIT ORDER:          │                  │                      │                 │
                        │                  │ INSERT client_order   │                 │
                        │                  │ (status: PENDING)     │                 │
                        │                  │ ──────────────────────┼────────────────►│
                        │                  │                       │                 │
                        │                  │ Add to OrderBook      │                 │
                        │                  │ (price-time priority  │                 │
                        │                  │  heap per instrument) │                 │
                        │                  │                       │                 │
    │ {order_id,        │                  │                       │                 │
    │  status: PENDING} │                  │                       │                 │
    │ ◄────────────────│◄─────────────────│                       │                 │
                        │                  │                       │                 │
                        │       Background (every 1.5s):          │                 │
                        │                  │ GET /prices/current   │                 │
                        │                  │ ─────────────────────►│                 │
                        │                  │ ◄─────────────────────│                 │
                        │                  │                       │                 │
                        │                  │ check_fills():        │                 │
                        │                  │   BUY fills when      │                 │
                        │                  │   limit_price >= ask  │                 │
                        │                  │                       │                 │
                        │                  │   SELL fills when     │                 │
                        │                  │   limit_price <= bid  │                 │
                        │                  │                       │                 │
                        │                  │ (if triggered, same   │                 │
                        │                  │  match-and-route flow │                 │
                        │                  │  as market orders)    │                 │


  CANCEL ORDER:         │                  │                      │                 │
    │ DELETE             │                  │                      │                 │
    │ /api/orders/{id}  │                  │                      │                 │
    │ ─────────────────►│ DELETE            │                      │                 │
    │                   │ /orders/{id}     │                      │                 │
    │                   │ ────────────────►│                      │                 │
    │                   │                  │ Flag in OrderBook     │                 │
    │                   │                  │ UPDATE CANCELLED      │                 │
    │                   │                  │ ──────────────────────┼────────────────►│
    │ {status:          │                  │                       │                 │
    │  CANCELLED}       │                  │                       │                 │
    │ ◄────────────────│◄─────────────────│                       │                 │
```

**Order statuses:** `PENDING` -> `MATCHED` -> `SUBMITTED` -> `FILLED` (or `REJECTED` / `CANCELLED` at any point)

**Order book:** Limit orders are managed in an in-memory order book using Python heaps. BUY side uses a max-heap (highest price, earliest time fills first). SELL side uses a min-heap (lowest price, earliest time fills first). Cancelled orders are lazily removed during fill checks.

## API Reference

### Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/auth/register` | No | Create account. Body: `{username, password, email?}` |
| POST | `/api/auth/login` | No | Login. Body: `{username, password}`. Returns: `{token, user_id}` |
| GET | `/api/auth/me` | JWT | Current user info |

### Market Data

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/prices?instruments=EUR_USD,GBP_USD` | JWT | Current bid/ask/mid/spread |
| GET | `/api/prices/candles?instrument=EUR_USD&granularity=H1&count=100` | JWT | OHLCV candle data |
| GET | `/api/prices/instruments` | JWT | List supported instruments |
| WS | `/ws/prices?token=<jwt>` | JWT (query) | Real-time price stream (1.5s interval) |

### Trading

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/orders` | JWT | Submit order. Body: `{instrument, side, order_type, quantity, limit_price?}` |
| GET | `/api/orders?status=FILLED` | JWT | List orders (optional status filter) |
| GET | `/api/orders/{id}` | JWT | Get order with LP fill details |
| DELETE | `/api/orders/{id}` | JWT | Cancel pending order |
| GET | `/api/trades/open` | JWT | Open trades (FILLED orders) |
| GET | `/api/trades/closed` | JWT | Closed trades |
| GET | `/api/account/summary` | JWT | Balance and open trade count |

## Database Schema

Three tables in PostgreSQL:

- **`users`** — `id` (UUID), `username`, `password_hash` (bcrypt), `email`, `balance` (default $100,000), `created_at`, `last_login`
- **`client_orders`** — `id` (UUID), `user_id` (FK), `instrument`, `side`, `order_type`, `quantity`, `limit_price`, `status`, `matched_price`, `fill_price`, timestamps
- **`lp_orders`** — `id` (UUID), `client_order_id` (FK), `lp_name` ("simulator"), `lp_order_id`, fill details, `status`, timestamps

Indexed on: `user_id`, `status`, `instrument` (client_orders); `client_order_id`, `status` (lp_orders).

## Quick Start

Requires Python 3.11+ and a running PostgreSQL instance (see `.env.example` for connection defaults).

```bash
# Install dependencies (from each service directory)
pip install -r services/api-gateway/requirements.txt
pip install -r services/price-service/requirements.txt
pip install -r services/engine/requirements.txt

# Start each service (in separate terminals)
cd services/price-service && uvicorn app.main:app --port 8001
cd services/engine && uvicorn app.main:app --port 8002
cd services/api-gateway && uvicorn app.main:app --port 8000
```

Services:
- API Gateway: http://localhost:8000
- Price Service: http://localhost:8001
- Engine: http://localhost:8002

Containerization and deployment are part of the learning journey — see the lab guide.

## Documentation

- [Docker Guide](docs/DOCKER.md)
- [Infrastructure (Terraform)](docs/infra.md)
- [GitHub Actions Workflows](docs/gha.md)
- [CI/CD Flow](docs/cicd-flow.md)
