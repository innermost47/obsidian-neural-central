# OBSIDIAN Neural — Distributed GPU Provider Network

### Overview

OBSIDIAN Neural runs on a distributed GPU provider network. Each provider makes their GPU available for real-time audio generation. Subscription revenue is redistributed **strictly equally** among all eligible providers each month via Stripe Connect, after deduction of a transparent platform fee covering infrastructure costs (fal.ai, hosting, maintenance).

If no provider is available, the system automatically falls back to fal.ai.

---

### Architecture

```
VST Client
    ↓ POST /api/v1/generate
FastAPI central server
    ↓ ping provider pool (random)
    ├── Provider available → local generation
    │       ↓ FFmpeg validation
    │       ↓ write public ownership JSON
    │       ↓ return WAV to client
    └── No provider → fal.ai fallback
```

---

### Environment Variables (central server)

| Variable                   | Default | Description                                  |
| -------------------------- | ------- | -------------------------------------------- |
| `PLATFORM_FEE_PCT`         | `0.15`  | Platform fee (15%) — covers fal.ai + hosting |
| `PING_PROBABILITY`         | `0.60`  | Probability of ping each hour                |
| `MIN_UPTIME_SCORE`         | `0.60`  | Minimum uptime to be eligible                |
| `MIN_BILLABLE_JOBS`        | `1`     | Minimum real jobs in the month               |
| `RANDOM_DELAY_MAX_MINUTES` | `50`    | Max random delay before ping execution       |
| `PING_TIMEOUT`             | `5.0`   | Ping timeout in seconds                      |

---

### Random Ping System

To prevent providers from cheating by turning on their server only at predictable ping times:

- Scheduler fires **every hour**
- **60% chance** the ping is actually sent (configurable via `PING_PROBABILITY`)
- **Random delay** of 0 to 50 minutes before execution (configurable via `RANDOM_DELAY_MAX_MINUTES`)
- Pings are logged in `provider_pings`
- `uptime_score` is recalculated after each ping wave

In parallel, the provider sends an automatic **heartbeat** to the central server every 5 minutes, updating `last_seen` independently of pings.

---

### Monthly Redistribution Eligibility

A provider is eligible if:

1. **uptime_score ≥ 60%** — responded to at least 60% of random pings that month
2. **billable_jobs ≥ 1** — processed at least 1 real job (not fal.ai fallback) that month

If no provider is eligible, no redistribution occurs.

---

### Stripe Redistribution

```
Monthly revenue
    - Platform fee (15%) → covers fal.ai + hosting + maintenance
    = Distributable amount
        ÷ nb eligible providers
        = Equal share per provider
```

Example with 180€ revenue and 6 eligible providers:

```
180€ - 27€ (15%) = 153€ distributable → 25.50€ per provider
```

The platform fee is published in `finances.json` at each redistribution — full transparency.

**Trigger redistribution (admin):**

```http
POST /api/v1/admin/providers/redistribution/compute
{
    "month_revenue_cents": 18000,
    "dry_run": true
}
```

Set `dry_run: false` to trigger actual Stripe transfers.

---

### Security

- **FFmpeg validation**: every WAV received from a provider is validated (format, duration 1-60s, max size 50MB) before being sent to the client
- **Immediate ban** if invalid WAV is returned
- **Per-provider API key**: auto-generated (`op_` + 48 random characters)
- **Text LLM → fal.ai only**: never processed at providers to prevent prompt injection
- **Public ownership JSON**: uses `public_user_id` (UUID, never the internal ID)
- **Authenticated heartbeat**: provider identifies itself via API key

---

### Admin Routes

| Method | Route                                            | Auth      | Description                   |
| ------ | ------------------------------------------------ | --------- | ----------------------------- |
| POST   | `/api/v1/admin/providers/heartbeat`              | API Key   | Provider heartbeat (internal) |
| GET    | `/api/v1/admin/providers/pool/status`            | ❌ public | Anonymized pool status        |
| GET    | `/api/v1/admin/providers/`                       | Admin     | List all providers            |
| POST   | `/api/v1/admin/providers/`                       | Admin     | Add a provider                |
| POST   | `/api/v1/admin/providers/redistribution/compute` | Admin     | Compute redistribution        |
| GET    | `/api/v1/admin/providers/{id}`                   | Admin     | Detail + recent jobs          |
| PATCH  | `/api/v1/admin/providers/{id}`                   | Admin     | Update a provider             |
| POST   | `/api/v1/admin/providers/{id}/activate`          | Admin     | Activate                      |
| POST   | `/api/v1/admin/providers/{id}/deactivate`        | Admin     | Deactivate                    |
| POST   | `/api/v1/admin/providers/{id}/ban`               | Admin     | Ban                           |
| POST   | `/api/v1/admin/providers/{id}/unban`             | Admin     | Unban                         |
| POST   | `/api/v1/admin/providers/{id}/regenerate-key`    | Admin     | New API key                   |
| DELETE | `/api/v1/admin/providers/{id}`                   | Admin     | Delete                        |
| GET    | `/api/v1/admin/providers/{id}/ping-stats`        | Admin     | Ping stats                    |

---

### Public Files

#### `public/finances.json`

```json
[
  {
    "month": "2026-03",
    "total_revenue_eur": 180.0,
    "platform_fee_pct": 15.0,
    "platform_fee_eur": 27.0,
    "distributable_eur": 153.0,
    "eligible_providers": 6,
    "share_per_provider_eur": 25.5,
    "remainder_eur": 0.0,
    "transfers": [
      {
        "provider_name": "Paul GPU",
        "amount_eur": 25.5,
        "uptime_score_pct": 87.5,
        "billable_jobs": 42,
        "status": "sent"
      }
    ],
    "published_at": "2026-04-01T00:00:00Z"
  }
]
```

---

### Public Statistics

The central server exposes the number of active subscribers in real time:

```
GET https://api.obsidian-neural.com/api/v1/public/stats
```

Response:

```json
{
  "paying_users": 42,
  "updated_at": "2026-03-28T14:32:00Z"
}
```

This endpoint is public and requires no authentication. It allows every provider to verify the platform's growth and estimate their monthly share.

Combined with `public/finances.json`, you get a complete and transparent view of the network's financial health:

| Source                   | Data                                  |
| ------------------------ | ------------------------------------- |
| `/api/v1/public/stats`   | Active subscribers count in real time |
| `/public/finances.json`  | Monthly redistribution history        |
| `/public/ownership.json` | Proof of generation ownership         |
