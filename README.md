# Freedom GP$ API — v1

**Freedom GP$** is a personal financial operating system: it organizes and
navigates money, shows what happens next, and recommends the best next
move — so a person always knows what they own, what they owe, when they
will be debt free, and what to pay next.

This repository is the **v1 production backend**: a FastAPI application
with a single scoring engine and API ecosystem that powers the web app,
mobile app, advisor dashboard, sovereign analysis mode, and nonprofit
mode — and eventually the full OmniSight intelligence platform.

> **Pricing is NOT final.** No billing, subscription, or payment code is
> wired into this repository. Prices are still being decided; do not treat
> any figure in docs or examples as a price.

## Stack

- **FastAPI** (OpenAPI 3.1) + **Pydantic v2**
- **SQLAlchemy 2.0 async** + **Alembic** migrations
- **PostgreSQL** (asyncpg)
- **Redis** + **Celery** (cache / background jobs)
- **JWT** authentication (python-jose, bcrypt via passlib)
- **Docker** + **docker-compose** (api + postgres + redis, behind nginx)
- **pytest** + **GitHub Actions** CI

## Quick start (Docker)

```bash
cp .env.example .env        # fill in real values; never commit a real .env
docker compose up --build
```

- API: http://localhost:8000
- Docs (OpenAPI 3.1): http://localhost:8000/docs
- Through nginx: http://localhost/docs

Apply migrations (inside the api container or locally):

```bash
docker compose exec api alembic upgrade head
```

## Run locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# start PostgreSQL + Redis first (or point DATABASE_URL / REDIS_URL at yours)
alembic upgrade head
uvicorn app.main:app --reload
```

## Run tests

```bash
python -m pytest -q
```

The suite covers every scoring engine with hand-computed expected values
plus the public API endpoints (root, health, scores, OmniSight signals,
Shadow Ledger valuation, scenario stress test). The score endpoints need
no database and no auth, so the suite runs without a live PostgreSQL.

## Endpoints

### Meta

| Method | Path | Description |
|---|---|---|
| GET | `/` | System + version (`{"system": "Freedom GP$", "version": "1.0"}`) |
| GET | `/health` | Liveness probe (`{"status": "ok"}`) |

### Scores (public — pure computation, no auth)

| Method | Path | Description |
|---|---|---|
| POST | `/scores/stinnes` | Stinnes Score — is debt creating wealth? |
| POST | `/scores/asset-flipper` | Asset Flipper Score — how hard are assets working? |
| POST | `/scores/antifragility` | Anti-Fragility Index — how do you perform under stress? |
| POST | `/scores/faustian` | Faustian Ratio — future pain vs current gain |
| POST | `/scores/broken-safe-haven` | Broken Safe Haven Index — trust vs actual protection |
| POST | `/scores/regulatory-friction` | Regulatory Friction Score — how trapped is value? |
| POST | `/scores/credit-health` | Credit Health Score — can you borrow? |
| POST | `/scores/sovereignty` | Sovereignty Score — can you live independent of a paycheck? |
| POST | `/scores/revaluation-gap` | Revaluation Gap — how much hidden upside exists? |
| POST | `/scores/wealth-index` | Freedom GP$ Wealth Index (weighted master formula) |

### Users (v1)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/users` | Create a user account (name, email, password) |
| POST | `/api/v1/users/login` | Log in, receive a JWT access token |
| GET | `/api/v1/users/{user_id}` | Fetch a user (own record only, auth required) |

### Ledger (v1, auth required)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/assets` | Add an asset |
| GET | `/api/v1/assets/{user_id}` | List a user's assets |
| POST | `/api/v1/liabilities` | Add a liability |
| GET | `/api/v1/liabilities/{user_id}` | List a user's liabilities |
| POST | `/api/v1/shadow-ledger/assets` | Add a hidden asset |
| POST | `/api/v1/shadow-ledger/value` | Value hidden assets + Hidden Asset Multiplier |
| POST | `/api/v1/reports` | Store a report |
| GET | `/api/v1/reports` | List the current user's reports |

### OmniSight + Scenarios

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/omnisight/economic-signals` | Economic signals + Economic Stress Score (illustrative values; labelled `source: "illustrative"`) |
| POST | `/api/v1/scenarios/stress-test` | Stress-test a wealth index against a named scenario (`2008`, `covid`, `inflation`) |

### State Vector (auth required, except the master-index computation)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/state-vector` | Record one snapshot of the caller's own state vector Ω(t) |
| GET | `/api/v1/state-vector/{user_id}` | List the caller's snapshots, latest first |
| GET | `/api/v1/state-vector/{user_id}/latest` | The caller's latest snapshot (404 when none recorded) |
| POST | `/api/v1/state-vector/master-index` | Pure computation (no DB, no auth): GP$ Master Index + Unified Prosperity Equation for one supplied state vector |

## Scoring engines

All engines live in `app/engines/` as pure functions (numbers in, score
out) so they are unit-testable in isolation:

- **Stinnes Score** — `(productive + hidden assets) / (debt principal +
  annual debt cost + risk factor)`; 999.0 when the denominator is 0.
  Bands: Debt Trap <1, Stable <2, Productive <4, Asset Arbitrage <8,
  Financial Sovereignty 8+.
- **Asset Flipper Score** — `(productive + hidden) / total assets * 100`.
  Bands: Hoarder 0-20, Underutilized 20-40, Stable 40-60, Productive
  60-80, Wealth Engine 80-100.
- **Anti-Fragility Index** — `stressed net worth / current net worth`.
  Bands: Fragile <0.50, Vulnerable 0.50-0.75, Resilient 0.75-1.00,
  Anti-Fragile 1.00+.
- **Faustian Ratio** — `future cost / present benefit`. Bands: Good <1,
  Caution 1-2, Dangerous 2-5, Faustian 5+.
- **Broken Safe Haven Index** — `expected protection - actual protection`.
- **Regulatory Friction Score** — average of tax, compliance, liquidity,
  ownership, and capital-control friction (0-100).
- **Sovereignty Score** — `passive income / required living expenses`.
  Bands: Dependent 0-0.5, Transitional 0.5-1, Sovereign 1-2, Fully
  Sovereign 2+.
- **Credit Health Score** — `(payment history * 0.35 + utilization * 0.30
  + age * 0.15 + mix * 0.10 + inquiry * 0.10) * 100`, scale 0-850.
- **Hidden Asset Multiplier** — `(IP + skills + licenses + network +
  brand + education + future royalties) / reported net worth`.
- **Revaluation Gap** — `potential value / current recognized value`.
- **Wealth Index** (FastAPI version) —
  `wealth = stinnes*20 + asset_flipper*2 + anti_fragility*100 +
  sovereignty*50`; `penalties = regulatory_friction +
  behavioral_friction + faustian_ratio*10`; `wealth_index = wealth -
  penalties`.
- **Economic Stress Score** (OmniSight) — `inflation*0.25 + rates*0.20 +
  unemployment*0.20 + debt_to_gdp*0.20 + money_supply_growth*0.15`.

### A note on the spec's example values

Two illustrative example outputs in the source spec do not reconcile
with their own formulas, so the tests assert the **formulas**, not the
examples:

- **Wealth Index 784**: the spec lists 784 for inputs (stinnes 4.88,
  asset flipper 82, anti-fragility 0.90, sovereignty 1.4, behavioral
  friction 22). The weighted formula yields ~335 for those inputs.
  `tests/test_wealth.py` asserts the formula value (346.86 for the
  test inputs) and documents the discrepancy.
- **Stinnes 5.61**: the spec lists 5.61 for inputs (productive 750,000;
  hidden 250,000; debt principal 150,000; annual carry cost 9,000; risk
  factor 20,000). The formula yields 5.59. The test asserts 5.59 and
  documents the discrepancy. The rating ("Asset Arbitrage") matches
  either way.

## Database entities (v1)

User, Profile, Asset, Liability, CashFlow, HiddenAsset, ScoreSnapshot,
Scenario, Report, OmniSightSignal, Recommendation, StateVector. See
`alembic/versions/0001_initial.py` for the v1 schema and
`alembic/versions/0002_state_vector.py` for the state vector table.

## State Vector Ω(t)

Every person, family, business, nonprofit, city, or nation can be
described as a state vector:

```
Ω(t) = [F, H, S, R, L, T, M, I, E]
```

| Variable | Meaning | Formula |
|---|---|---|
| F | Financial Capital | Assets − Liabilities |
| H | Human Capital | Knowledge + Skill + Experience |
| S | Social Capital | Network Centrality + Trust Density |
| R | Resource Capital | Σ Productive Assets |
| L | Legacy Capital | Preserved Knowledge + Future Influence |
| T | Trust Capital | Successful Interactions / Total Interactions |
| M | Memory Capital | Stored Information × Accessibility |
| I | Intelligence Capital | Correct Decisions / Total Decisions |
| E | Entropy | Loss Rate + Risk Exposure + System Friction |

One row of the `state_vectors` table is one snapshot of one user's
nine variables at one time, plus the Opportunity Vector value
(Potential Value = Current Value + Opportunity). The time series of
snapshots is the ledger's trajectory — and what forecasting reads.
Every variable is indexed by time, X(t): past, present, future.

Two computations run on a state vector (`app/engines/master_index.py`):

- **Unified Prosperity Equation** — `P = (F+H+S+R+L) × (T+I+M) / E`.
  Capitals create value; trust, intelligence, and memory amplify and
  preserve it; entropy destroys it. Returns `null` when E is 0 — the
  equation is undefined there and no value is invented.
- **GP$ Master Index** —
  `GP$ = αF + βH + γS + δR + εL + ζT + ηM + θI − λE`.

> **The Master Index coefficients are UNCALIBRATED PLACEHOLDERS.**
> They default to equal weight 1.0 and must be **learned statistically**
> (regression analysis, optimization theory, machine learning, Bayesian
> inference, system dynamics) from real user data — not guessed,
> measured. Until calibration, every Master Index response reports
> `calibrated: false` and carries the warning. Never present a
> placeholder-coefficient index as calibrated.

## Project structure

```
freedom-gps-v1/
├── app/
│   ├── main.py            # FastAPI app ("Freedom GP$ API", v1.0)
│   ├── config.py          # settings from env
│   ├── database.py        # async SQLAlchemy engine + session
│   ├── auth.py            # JWT + bcrypt
│   ├── models/            # SQLAlchemy 2.0 models (v1 entities)
│   ├── schemas/           # Pydantic v2 request/response models
│   ├── engines/           # pure scoring functions
│   └── api/               # FastAPI routers
├── alembic/               # migrations (0001_initial creates all v1 tables)
├── tests/                 # pytest suite
├── .github/workflows/ci.yml
├── Dockerfile
├── docker-compose.yml     # api + postgres + redis + nginx
├── nginx.conf
├── requirements.txt
└── .env.example
```

## Security notes

- Never commit a real `.env`. Change `JWT_SECRET_KEY` in production.
- Passwords are bcrypt-hashed; tokens are HS256 JWTs.
- The OmniSight economic signals endpoint returns **illustrative demo
  values** until live feeds are wired in; the response always reports
  `"source": "illustrative"` so consumers never mistake them for live
  data.
- No real personal financial numbers and no pricing/billing code are in
  this repository.
