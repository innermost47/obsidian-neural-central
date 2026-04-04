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

### Security

#### General

- **FFmpeg validation**: every WAV received from a provider is validated (format, duration 1-60s, max size 50MB) before being sent to the client
- **Immediate ban** if invalid WAV is returned
- **Proof-of-work verification**: periodic mel fingerprint comparison against an encrypted reference bank ensures providers run a genuine model
- **Encrypted fingerprint bank**: reference fingerprints stored with a dedicated Fernet key (`SAMPLE_ENCRYPTION_KEY`) — independent of other keys, a leak of one does not compromise the others
- **Per-provider API key**: auto-generated (`op_` + 48 random characters)
- **Text LLM → fal.ai only**: never processed at providers to prevent prompt injection
- **Public ownership records**: use `public_user_id` (UUID, never the internal ID), stored in DB
- **Authenticated heartbeat**: provider identifies itself via API key

#### Provider ↔ Central Server Communication Security

**Unified `/process` endpoint** — All provider operations (health, status, generate) route through a single `POST /process` endpoint with an `action` field. This minimizes attack surface and simplifies validation.

**Strict Pydantic schema validation**:

| Endpoint                        | Request                                                | Response                                                                                                                  | Notes                                                |
| ------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `POST /process?action=health`   | `{"action": "health"}`                                 | `ProviderHealthResponse` with `status="ok"`, `model_loaded`, `model`, `model_id`                                          | All fields mandatory, enum-validated                 |
| `POST /process?action=status`   | `{"action": "status"}`                                 | `ProviderStatusResponse` with availability, API key (48-64 chars), model/device enums, VRAM                               | All fields mandatory, enum-validated                 |
| `POST /process?action=generate` | `{"action": "generate", "prompt", "duration", "seed"}` | HTTP 200 with WAV body + headers: `X-Provider-Key`, `X-Model`, `X-Duration`, `X-Sample-Rate`, `X-Seed`, `X-Provider-Hash` | All headers validated via `ProviderGenerateResponse` |

**Request validation**:

- `action` is an enum: only `"health"`, `"status"`, `"generate"` accepted
- `prompt` required only for `generate`, max length enforced
- `duration` clamped to [2, 30] seconds
- `seed` validated as 0 ≤ seed ≤ 2^31-1
- Extra fields rejected via `extra="forbid"` — any unknown field causes immediate rejection and ban

**Response validation (Pydantic models)**:

```python
class SupportedModel(str, Enum):
    STABLE_AUDIO = "stable-audio-open-1.0"

class SupportedModelId(str, Enum):
    STABLE_AUDIO_ID = "stabilityai/stable-audio-open-1.0"

class SupportedDevice(str, Enum):
    CUDA = "cuda"

class ProviderStatusResponse(BaseModel):
    available: bool
    api_key: str = Field(..., min_length=48, max_length=64)
    model: SupportedModel  # Enum-validated: must be exact value
    model_id: SupportedModelId  # Enum-validated: must be exact value
    device: SupportedDevice  # Enum-validated: must be "cuda"
    generating: bool
    vram_total_gb: float = Field(..., ge=0, le=999999)
    vram_used_gb: float = Field(..., ge=0, le=999999)
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

class ProviderGenerateResponse(BaseModel):
    api_key: str = Field(..., min_length=48, max_length=64)
    model: SupportedModel
    duration: int = Field(..., ge=2, le=30)
    sample_rate: int = Field(..., ge=44100, le=48000)
    seed: int = Field(..., ge=0, le=2**31 - 1)
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")
```

- Any field missing, wrong type, or out of range → `ValidationError` → provider **auto-banned**
- Extra fields in response → rejected → provider **auto-banned**
- Enum mismatch (e.g., wrong model name) → provider **auto-banned**

**Heartbeat endpoint** (`POST /providers/heartbeat`):

- Accepts **ONLY** `True` as JSON body
- Any other payload (object, array, string, etc.) → `400 Bad Request` + provider **auto-banned**
- Returns bare `True`
- No conversation, no info leakage

**WebSocket connection** (`/providers/connect`):

- Provider establishes connection with `X-Provider-Key` header (mandatory, validated)
- Provider **sends nothing** except auto-ping frames (WebSocket protocol level)
- If provider sends any application-level message → **immediate ban with reason "Unsolicited message on WebSocket"**
- Server never sends messages to provider (only automatic ping/pong)
- Provider listens only for connection lifecycle events
- Connection stays alive via automatic WebSocket ping/pong (no application-level messages)

**Code integrity verification** (`X-Provider-Hash` header):

- Provider computes SHA256 hash of its own source code (whitespace/comments stripped) + API key hash + shared secret
- Hash is included in all response headers: `X-Provider-Hash`
- Central server verifies hash matches expected value for that provider
- Hash mismatch → immediate ban with reason "Code integrity check failed"
- Note: This is a deterrent to casual modifications, not cryptographically bulletproof, but combined with behavioural validation (determinism, seed verification) provides defense-in-depth

**Content-Type validation**:

- Generate response must have `Content-Type: audio/wav` or `application/octet-stream`
- Invalid content-type → immediate ban with reason "Invalid content-type in response"

**Canary testing** (`randomly_test_providers`):

- Runs as a background task, independently of the verification loop
- At random intervals (1h–6h), tests ~1/3 of active providers, chosen at random
- Each session draws a **random subset** of tests (4–10 regular + 4–8 canary), **shuffled together** — canary requests are indistinguishable from regular ones by position or timing
- Regular invalid requests cover:
  - Unknown `action` values
  - Missing required fields (e.g., no `action`, no `prompt` on generate)
  - Extra fields (rejected by `extra="forbid"`)
  - Out-of-range `duration` (too low, too high)
  - Invalid `seed` (negative, above 2^31-1, wrong type)
  - Empty `prompt` on generate
- **Canary actions are randomly generated** (`secrets.token_hex(6)`) at each session — unpredictable even with full access to the source code. A provider cannot anticipate or whitelist them without breaking its own validation logic. If any canary action is accepted (i.e., does not return 422), it signals that action validation has been tampered with → provider is **immediately banned** with reason `"Canary test failed: provider accepted invalid action (code modification detected)"`
- Inter-test delays are randomised (0.5s–3s per request, 0s–2h before the first test) to prevent timing fingerprinting
- Each provider is tested in its own isolated DB session to avoid long-lived session state issues

**Automatic banning on any protocol violation**:

- Invalid Pydantic schema → ban with "Invalid response headers format"
- Missing required header → ban with "Missing [header] header"
- Hash mismatch → ban with "Code integrity check failed on generate"
- Invalid content-type → ban with "Invalid content-type in response"
- Unsolicited WebSocket message → ban with "Unsolicited message on WebSocket"
- Heartbeat payload != True → ban with "Invalid heartbeat payload"
- Canary action accepted → ban with "Canary test failed: provider accepted invalid action (code modification detected)"

All bans trigger:

1. Admin notification email with provider name, ID, and reason
2. Provider notification email with remediation guidance and common causes
3. Provider downgraded from "provider" tier to "free"
4. Provider marked `is_banned = True` and `is_active = False`

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

```

```
