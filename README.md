# OBSIDIAN Neural — Distributed GPU Provider Network

### Related Repositories

| Repository                                                                                           | Description                                  |
| ---------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **[obsidian-neural-central](https://github.com/innermost47/obsidian-neural-central)** ← you are here | Central inference server                     |
| [obsidian-neural-provider](https://github.com/innermost47/obsidian-neural-provider)                  | Provider kit — run a GPU node on the network |
| [obsidian-neural-frontend](https://github.com/innermost47/obsidian-neural-frontend)                  | Storefront & dashboard                       |
| [obsidian-neural-controller](https://github.com/innermost47/obsidian-neural-controller)              | Mobile MIDI controller app                   |
| [ai-dj](https://github.com/innermost47/ai-dj)                                                        | VST3/AU plugin (client)                      |

---

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
    │       ↓ write ownership record (DB)
    │       ↓ return WAV to client
    └── No provider → fal.ai fallback
```

---

### Environment Variables (central server)

| Variable                   | Default | Description                                      |
| -------------------------- | ------- | ------------------------------------------------ |
| `PLATFORM_FEE_PCT`         | `0.15`  | Platform fee (15%) — covers fal.ai + hosting     |
| `PING_PROBABILITY`         | `0.60`  | Probability of ping each hour                    |
| `MIN_UPTIME_SCORE`         | `0.80`  | Minimum uptime to be eligible                    |
| `MIN_BILLABLE_JOBS`        | `1`     | Minimum real jobs in the month                   |
| `RANDOM_DELAY_MAX_MINUTES` | `50`    | Max random delay before ping execution           |
| `PING_TIMEOUT`             | `5.0`   | Ping timeout in seconds                          |
| `VERIFY_TIMEOUT`           | `120.0` | Verification request timeout in seconds          |
| `VERIFY_POOL_PCT`          | `0.30`  | Fraction of active providers verified per round  |
| `SIMILARITY_THRESHOLD`     | `0.98`  | Minimum cosine similarity to pass verification   |
| `MAX_CONSECUTIVE_FAILS`    | `3`     | Consecutive failures before automatic ban        |
| `VERIFY_DURATION`          | `5`     | Audio duration used for fingerprinting (seconds) |
| `VERIFY_INTERVAL_MIN`      | `3600`  | Min delay between verification rounds (seconds)  |
| `VERIFY_INTERVAL_MAX`      | `18000` | Max delay between verification rounds (seconds)  |

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

### Provider Verification (Proof-of-Work)

To ensure providers are running a genuine, unmodified model and not relaying requests or faking responses, the central server runs periodic **proof-of-work verification rounds**.

#### How it works

1. A random subset of active providers is selected (default: 30% of the pool, minimum 2)
2. A random **prompt** and **seed** are chosen from a fixed bank of 64 reference prompts
3. Each selected provider receives `POST /verify` with `{ prompt, seed, duration }`
4. The server computes a **mel spectrogram fingerprint** (128 mel bands, averaged over time) for each returned WAV
5. Providers are **grouped by model name** (returned in the `X-Model` response header)
6. Within each group, fingerprints are compared to the group mean via **cosine similarity**
7. A provider passes if `similarity ≥ 0.98` (configurable via `SIMILARITY_THRESHOLD`)

#### Scoring and banning

- A **pass** resets the provider's consecutive failure counter
- A **fail** increments `verification_failures`
- After **3 consecutive failures** (configurable via `MAX_CONSECUTIVE_FAILS`), the provider is **automatically banned**
- A provider that fails to respond at all counts as a failed verification
- If a provider is the **sole node on its model**, it is skipped for comparison and auto-passes (cannot be compared against a group of one)

#### Timing

Rounds run on a **random interval between 1h and 5h** (configurable via `VERIFY_INTERVAL_MIN` / `VERIFY_INTERVAL_MAX`), launched as a background loop at server startup. The randomness prevents providers from predicting when the next check will occur.

#### Results

All verification results are stored in the `provider_verifications` table.

```
provider_verifications
├── provider_id
├── prompt
├── seed
├── similarity_score   (null if no response)
├── passed
└── verified_at
```

---

### Monthly Redistribution Eligibility

A provider is eligible if:

1. **uptime_score ≥ 80%** — responded to at least 80% of random pings that month
2. **billable_jobs ≥ 1** — processed at least 1 real job (not fal.ai fallback) that month

If no provider is eligible, no redistribution occurs.

---

### Stripe Redistribution

```
Monthly revenue (fetched from Stripe API — all succeeded charges)
    - Platform fee (15%) → covers fal.ai + hosting + maintenance
    = Distributable amount
        ÷ nb eligible providers
        = Equal share per provider
```

Example with 180€ revenue and 6 eligible providers:

```
180€ - 27€ (15%) = 153€ distributable → 25.50€ per provider
```

The redistribution report is saved to the `finance_reports` table at each execution — full transparency.

**Automatic redistribution runs via cron on the 1st of each month** — see Cron Tasks below.

---

### Security

- **FFmpeg validation**: every WAV received from a provider is validated (format, duration 1-60s, max size 50MB) before being sent to the client
- **Immediate ban** if invalid WAV is returned
- **Proof-of-work verification**: periodic mel fingerprint comparison ensures providers run a genuine model
- **Per-provider API key**: auto-generated (`op_` + 48 random characters)
- **Text LLM → fal.ai only**: never processed at providers to prevent prompt injection
- **Public ownership records**: use `public_user_id` (UUID, never the internal ID), stored in DB
- **Authenticated heartbeat**: provider identifies itself via API key

---

### Public Data Dashboard

All network data — active subscribers, monthly redistribution history, and proof-of-generation logs — is published live at:

**[obsidian-neural.com/public.html](https://obsidian-neural.com/public.html)**

No authentication required. No data is ever deleted.

---

### Cron Tasks

All tasks run via a wrapper script that ensures the correct environment is loaded.

**1. Create the wrapper script** `/path/to/project/run_cron.sh`:

```bash
#!/bin/bash
export ENV=prod
cd /path/to/project
/path/to/project/venv/bin/python /path/to/project/cron_daily.py "$@"
```

```bash
chmod +x /path/to/project/run_cron.sh
```

**2. Add to crontab** (`crontab -e`):

```cron
0 10 * * * /path/to/project/run_cron.sh --task followup_emails >> /path/to/logs/cron.log 2>&1
0 * * * * /path/to/project/run_cron.sh --task expiration_warnings >> /path/to/logs/cron.log 2>&1
0 0 * * * /path/to/project/run_cron.sh --task expire_gifts >> /path/to/logs/cron.log 2>&1
5 0 * * * /path/to/project/run_cron.sh --task refill_gifts >> /path/to/logs/cron.log 2>&1
10 0 1 * * /path/to/project/run_cron.sh --task refill_provider_credits >> /path/to/logs/cron.log 2>&1
0 3 1 * * /path/to/project/run_cron.sh --task cleanup_pings >> /path/to/logs/cron.log 2>&1
0 6 1 * * /path/to/project/run_cron.sh --task redistribution >> /path/to/logs/stripe_payout.log 2>&1
```

| Task                      | Schedule              | Description                                                                                |
| :------------------------ | :-------------------- | :----------------------------------------------------------------------------------------- |
| `followup_emails`         | Every day at 10:00    | Onboarding sequence (D+2, D+7, weeks 2–4)                                                  |
| `expiration_warnings`     | Every hour            | Warns users 7, 3, and 1 day(s) before subscription expiry                                  |
| `expire_gifts`            | Every day at 00:00    | Expires active gift subscriptions past their end date                                      |
| `refill_gifts`            | Every day at 00:05    | Monthly credit refill for active gift subscriptions                                        |
| `refill_provider_credits` | 1st of month at 00:10 | Monthly credit refill for provider accounts                                                |
| `cleanup_pings`           | 1st of month at 03:00 | **DB Cleanup**: Deletes ping logs older than 2 months (in 5k batches) to keep queries fast |
| `redistribution`          | 1st of month at 06:00 | Fetches Stripe revenue → Computes prorated uptime → Executes Stripe transfers              |
