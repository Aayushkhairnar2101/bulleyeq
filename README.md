# BullEyeQ

BullEyeQ is a testnet-only algorithmic trading terminal scaffold for Delta Exchange India. It combines a FastAPI backend, a React/Vite interface, indicator calculations, local audit persistence, and a Delta v2 REST client. It deliberately shows empty states when data is unavailable; it does not invent balances, fills, or performance.

> **Execution status:** Starting the bot runs a background volume-breakout strategy worker against Delta India testnet. Entries are limited to one contract, use native bracket stop-loss/take-profit values, and require a confirmed candle and a passing equity risk check. A Trading-permission Demo API key is required. Verify exit protection on the exchange response. This is not a claim of profitability.

## Current capabilities

- Backend calls are pinned to `https://cdn-ind.testnet.deltaex.org`; configuration rejects any other host.
- Delta v2 public product, ticker, and candle routes and authenticated balance, position, and open-order reads.
- Delta request signing stays in the backend. Credentials are loaded from environment variables and never returned to the frontend.
- Bot start launches a persisted background worker; it evaluates only confirmed, fresh candles and pauses on unavailable account/risk data, stale markets, ambiguous order submission, or unconfirmed exits.
- Entries are capped at one contract, reject any existing account position/open order, attach ATR-based stop-loss/take-profit values, and use a unique client order ID for timeout reconciliation.
- One closed-candle strategy combining 20-candle breakouts, relative volume, rolling VWAP, EMA/SMA trend, MACD, RSI, and ATR risk levels, with explainable rejection reasons.
- SQLite persistence for strategy/risk configuration, events, daily equity baselines, and signal decisions.
- Control endpoints require a private bearer token; live start/resume are blocked until execution is implemented.
- React terminal with chart, environment status, connection states, audit events, and no fabricated account metrics.

## Architecture

```text
src/                       React + TypeScript terminal
backend/app/main.py         FastAPI routes and application lifecycle
backend/app/exchange/       Delta Exchange India testnet client
backend/app/strategy/       EMA/SMA/RSI/ATR and signal evaluation
backend/app/risk/           Persisted daily baseline and limit calculation
backend/app/database.py     SQLite configuration and audit tables
tests/                      Backend unit tests
```

## Run locally

Requirements: Python 3.11+, Node.js 20+, npm.

1. Create a Python virtual environment and install dependencies:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`. Set a unique `APP_AUTH_TOKEN`. Add API credentials created in the Delta **Demo** account only when you are ready to connect. Never put credentials in the frontend.

3. Start the backend from the project root:

   ```powershell
   uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
   ```

4. In another terminal, install and start the frontend:

   ```powershell
   npm install
   npm run dev
   ```

Open `http://127.0.0.1:5173`. API documentation is available at `http://127.0.0.1:8000/docs`.

## Delta testnet connection

Create keys from Delta Exchange's Demo/Testnet account. For account reads, enable Read Data; manual order entry also requires Trading permission and the IP whitelist Delta requests when creating a trading key. This build reads the API key and secret only in FastAPI. Use the India testnet host documented by Delta: `https://cdn-ind.testnet.deltaex.org`. The Global demo uses a different host and is intentionally unsupported by this fixed India-testnet client.

The signing implementation follows Delta v2's HMAC SHA-256 authentication model (`method + timestamp + request path/query + body`) and sends `api-key`, `timestamp`, and `signature` headers. Review the current [official API documentation](https://docs.delta.exchange/) before connecting; Delta may change its API. The API Connection screen never accepts or stores secrets.

## Strategy

Configured defaults: 5-minute candles; break above/below the prior 20-candle range; current volume at least 1.5× the preceding 20-candle average; rolling 20-candle VWAP; EMA 9/21 and SMA 50 trend alignment; MACD confirmation; RSI 52–75 for longs and 25–48 for shorts; ATR 14 stop distance at 1.5× ATR; 2:1 reward/risk. Signals use confirmed candles only. Missing volume disables entries. `/api/strategy/signal` records a view-only decision; the background worker applies risk gates before submitting any entry. These are test parameters, not a profit guarantee.

## Risk model

The risk calculator uses the reporting timezone day and persists the first observed equity as its daily baseline. Net PnL is `realized + unrealized - fees + funding` (funding must be supplied with its signed value). The threshold amounts are baseline multiplied by configured percentages. A breached loss/profit threshold is persisted as a daily lock. Current exchange account data is required before a baseline can be established. A reporting timezone defaults to `Asia/Kolkata`.

The worker reads Delta wallet `meta.net_equity` and compares it with a persisted daily baseline to enforce the daily loss/profit locks. Its displayed PnL basis is exchange net-equity change, which includes unrealized equity changes but does not separately classify realized PnL, fees, and funding. Locks pause entries and persist across restarts. Limits cannot protect against gaps, slippage, rejected requests, or connectivity failures.

## API overview

- `GET /api/health`, `/api/exchange/status`, `/api/account/summary`
- `POST /api/exchange/test-connection` (authorized)
- `GET /api/market/products`, `/api/market/ticker`, `/api/market/candles`
- `POST /api/bot/start` (authorized; starts automatic testnet execution after explicit UI confirmation)
- `GET/PUT /api/strategy/config`, `GET /api/strategy/signal`, `/api/strategy/decisions`
- `GET/PUT /api/risk/config`, `GET /api/risk/status`
- `GET /api/bot/status`, `POST /api/bot/{start|pause|resume|stop|kill-switch}`
- `GET /api/positions`, `/api/orders`, `/api/trades`, `/api/performance`, `/api/system/events`

All state-changing endpoints require `Authorization: Bearer <APP_AUTH_TOKEN>`. Do not expose the development server to the public internet. Add user identity and stronger session/CSRF protections before any hosted deployment.

## Testing

Run backend unit tests with `pytest`. Tests use local data and do not contact the exchange. Actual testnet integration checks should only be initiated manually with Demo account credentials. Never use production credentials.

## Demonstration checklist

1. Start backend and frontend; show the permanent TESTNET label and disconnected state.
2. Configure testnet credentials in `.env` and restart backend.
3. Verify the test connection and show real candle/account data where available.
4. Open strategy signal and decision log to explain evaluated indicator values.
5. Save the configured strategy and risk values; note the one-contract test limit.
6. Start the bot from the terminal after reading its testnet confirmation. The worker waits for a confirmed signal and validates account/risk state before submitting.
7. Verify accepted order and attached exit prices on Delta Demo; monitor/close the position there and review audit events in SQLite.

## Known limitations

- No WebSocket feed; market data uses REST requests.
- No authenticated user model; a single local bearer token protects controls.
- No WebSocket feed, partial-fill recovery, or in-app safe close workflow. The worker polls REST data; inspect attached brackets and manage/close positions in Delta Demo.
- Daily risk state cannot be presented as exchange-verified until equity, ledger adjustments, fees, and funding are normalized and reconciled.
- No trade history/performance is generated until actual executions exist.
- No backtest is provided; the hackathon prompt supplied no historical dataset.

This is a development implementation, not investment advice and not a guarantee of profit or maximum loss.
