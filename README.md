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

| Variable                      | Default | Description                                           |
| ----------------------------- | ------- | ----------------------------------------------------- |
| `PLATFORM_FEE_PCT`            | `0.15`  | Platform fee (15%) — covers fal.ai + hosting          |
| `PING_PROBABILITY`            | `0.60`  | Probability of ping each hour                         |
| `MIN_UPTIME_SCORE`            | `0.80`  | Minimum uptime to be eligible                         |
| `MIN_BILLABLE_JOBS`           | `1`     | Minimum real jobs in the month                        |
| `RANDOM_DELAY_MAX_MINUTES`    | `50`    | Max random delay before ping execution                |
| `PING_TIMEOUT`                | `5.0`   | Ping timeout in seconds                               |
| `VERIFY_TIMEOUT`              | `120.0` | Verification request timeout in seconds               |
| `VERIFY_POOL_PCT`             | `0.30`  | Fraction of active providers verified per round       |
| `SIMILARITY_THRESHOLD`        | `0.98`  | Minimum cosine similarity to pass verification        |
| `MAX_CONSECUTIVE_FAILS`       | `3`     | Consecutive failures before automatic ban             |
| `VERIFY_DURATION`             | `5`     | Audio duration used for fingerprinting (seconds)      |
| `VERIFY_INTERVAL_MIN`         | `3600`  | Min delay between verification rounds (seconds)       |
| `VERIFY_INTERVAL_MAX`         | `18000` | Max delay between verification rounds (seconds)       |
| `TRUSTED_SAMPLE_TARGET`       | `5`     | Number of reference samples to maintain in the bank   |
| `WAIT_FOR_FREE_TIMEOUT`       | `600`   | Max wait for a provider to finish a job before skip   |
| `WAIT_FOR_FREE_POLL_INTERVAL` | `15`    | Polling interval when waiting for a free provider (s) |
| `SAMPLE_ENCRYPTION_KEY`       | —       | Fernet key for encrypting fingerprints in the DB      |

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

#### Trusted provider (reference node)

One provider can be flagged `is_trusted = true` in the database (typically the operator's own machine). This node acts as the **absolute reference** for all verification rounds — its output is never compared against other providers, it is the ground truth.

- The trusted provider must be online (`is_online = true`) and not banned
- Before each round, the server checks whether the trusted node is free (`is_generating = false`, `is_disposable = true`) and locks it temporarily
- While locked, the server fills the **reference sample bank** up to `TRUSTED_SAMPLE_TARGET` samples by generating audio with different random prompts and seeds
- Each fingerprint is **encrypted with `SAMPLE_ENCRYPTION_KEY`** (Fernet) before being stored in `verification_samples` — a DB leak does not expose usable fingerprints
- The trusted node is **unlocked immediately** after the bank fill, before the actual test round fires, so it remains available for production jobs
- A random sample is then drawn from the bank as the reference for the current round

#### Fallback hierarchy

| Situation                         | Reference source                          |
| --------------------------------- | ----------------------------------------- |
| Trusted online and free           | Fill bank → draw random sample from bank  |
| Trusted online but busy           | Draw random sample from existing bank     |
| No trusted online, bank non-empty | Draw random sample from existing bank     |
| No trusted online, bank empty     | Round bypassed — no ban, wait for trusted |

#### Provider availability flags

Two flags on the `Provider` model control scheduling:

| Flag            | Meaning                                                                     |
| --------------- | --------------------------------------------------------------------------- |
| `is_generating` | Provider is currently processing a production job                           |
| `is_disposable` | Provider is available to be selected for a test (`false` = locked for test) |

Before any verification request is sent, the server:

1. Waits in parallel (up to `WAIT_FOR_FREE_TIMEOUT`) for all selected providers to finish their current job (`is_generating = false`)
2. Locks each free provider atomically (`is_disposable = false`) to prevent a production job from sneaking in
3. Fires all verification requests in parallel via `asyncio.gather`
4. Unlocks all providers in a `finally` block regardless of outcome

Production job dispatch (`_find_available_provider`) filters on `is_disposable = true`, so a provider under test is invisible to the job queue.

#### How verification works

1. A random subset of active, non-trusted providers is selected (default: 30% of the pool)
2. A reference fingerprint is drawn randomly from the sample bank (see above)
3. Each selected provider receives `POST /verify` with `{ prompt, seed, duration }`
4. The server computes a **mel spectrogram fingerprint** (128 mel bands, averaged over time) for each returned WAV
5. Each fingerprint is compared to the reference via **cosine similarity**
6. A provider running a **different model** than the reference is automatically skipped (no penalty — cross-model comparison is meaningless)

#### Scoring and banning

- A **pass** (`similarity ≥ SIMILARITY_THRESHOLD`) resets the provider's consecutive failure counter
- A **fail** increments `verification_failures`
- After **`MAX_CONSECUTIVE_FAILS` consecutive failures**, the provider is **automatically banned**
- A provider that fails to respond counts as a failed verification
- A provider busy beyond `WAIT_FOR_FREE_TIMEOUT` is **skipped for this round** with no penalty

#### Timing

Rounds run on a **random interval between 1h and 5h** (configurable via `VERIFY_INTERVAL_MIN` / `VERIFY_INTERVAL_MAX`). The randomness prevents providers from predicting when the next check will occur.

#### Results

All verification results are stored in the `provider_verifications` table.

```
provider_verifications
├── provider_id
├── prompt
├── seed
├── similarity_score   (null if no response or model mismatch)
├── passed
└── verified_at

verification_samples   (reference fingerprint bank)
├── prompt
├── seed
├── model
├── encrypted_fingerprint   (Fernet-encrypted numpy float32 array)
└── created_at
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
- **Proof-of-work verification**: periodic mel fingerprint comparison against an encrypted reference bank ensures providers run a genuine model
- **Encrypted fingerprint bank**: reference fingerprints stored with a dedicated Fernet key (`SAMPLE_ENCRYPTION_KEY`) — independent of other keys, a leak of one does not compromise the others
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
