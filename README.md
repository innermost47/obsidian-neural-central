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

OBSIDIAN Neural runs on a distributed GPU provider network. Each provider makes their GPU available for real-time audio generation and LLM inference. Subscription revenue is redistributed **strictly equally** among all eligible providers each month via Stripe Connect, after deduction of a transparent platform fee covering infrastructure costs (fal.ai, hosting, maintenance).

If no provider is available, the system automatically falls back to fal.ai.

---

### Architecture

```
VST Client
    ↓ POST /api/v1/generate
FastAPI central server
    ├── LLM inference (prompt optimization / drawing analysis)
    │       ↓ ping provider pool (random)
    │       ├── Provider available → Gemma 4 via Ollama
    │       │       ↓ echo verification (conversation integrity)
    │       │       ↓ semantic similarity check (nomic-embed-text, warn only)
    │       │       ↓ return optimized prompt
    │       └── No provider → fal.ai fallback
    │
    └── Audio generation
            ↓ ping provider pool (random)
            ├── Provider available → local GPU generation
            │       ↓ FFmpeg validation
            │       ↓ write ownership record (DB)
            │       ↓ return WAV to client
            └── No provider → fal.ai fallback
```

---

### Provider Capabilities

Each provider runs two inference stacks:

| Stack | Model                               | Purpose                                         |
| ----- | ----------------------------------- | ----------------------------------------------- |
| Audio | `stabilityai/stable-audio-open-1.0` | WAV generation from text prompts                |
| LLM   | `gemma4:e2b` via Ollama             | Prompt optimization + drawing-to-sound analysis |

Providers are **mutually exclusive** — a provider busy with audio cannot accept an LLM request and vice versa. The availability flag returned in `/process?action=status` reflects both states simultaneously.

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
| `SIMILARITY_THRESHOLD`        | `0.98`  | Minimum cosine similarity to pass audio verification  |
| `MAX_CONSECUTIVE_FAILS`       | `3`     | Consecutive failures before automatic ban             |
| `VERIFY_DURATION`             | `5`     | Audio duration used for fingerprinting (seconds)      |
| `VERIFY_INTERVAL_MIN`         | `3600`  | Min delay between verification rounds (seconds)       |
| `VERIFY_INTERVAL_MAX`         | `18000` | Max delay between verification rounds (seconds)       |
| `TRUSTED_SAMPLE_TARGET`       | `5`     | Number of reference samples to maintain in the bank   |
| `WAIT_FOR_FREE_TIMEOUT`       | `600`   | Max wait for a provider to finish a job before skip   |
| `WAIT_FOR_FREE_POLL_INTERVAL` | `15`    | Polling interval when waiting for a free provider (s) |
| `SAMPLE_ENCRYPTION_KEY`       | —       | Fernet key for encrypting fingerprints in the DB      |
| `LLM_COSINE_THRESHOLD`        | `0.60`  | Semantic similarity threshold for LLM warning logs    |

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

#### Audio verification

To ensure providers are running a genuine, unmodified model and not relaying requests or faking responses, the central server runs periodic **proof-of-work verification rounds**.

##### Trusted provider (reference node)

One provider can be flagged `is_trusted = true` in the database (typically the operator's own machine). This node acts as the **absolute reference** for all verification rounds — its output is never compared against other providers, it is the ground truth.

- The trusted provider must be online (`is_online = true`) and not banned
- Before each round, the server checks whether the trusted node is free (`is_generating = false`, `is_disposable = true`) and locks it temporarily
- While locked, the server fills the **reference sample bank** up to `TRUSTED_SAMPLE_TARGET` samples by generating audio with different random prompts and seeds
- Each fingerprint is **encrypted with `SAMPLE_ENCRYPTION_KEY`** (Fernet) before being stored in `verification_samples` — a DB leak does not expose usable fingerprints
- The trusted node is **unlocked immediately** after the bank fill, before the actual test round fires, so it remains available for production jobs
- A random sample is then drawn from the bank as the reference for the current round

##### Fallback hierarchy

| Situation                         | Reference source                          |
| --------------------------------- | ----------------------------------------- |
| Trusted online and free           | Fill bank → draw random sample from bank  |
| Trusted online but busy           | Draw random sample from existing bank     |
| No trusted online, bank non-empty | Draw random sample from existing bank     |
| No trusted online, bank empty     | Round bypassed — no ban, wait for trusted |

##### Provider availability flags

Two flags on the `Provider` model control scheduling:

| Flag                | Meaning                                                                     |
| ------------------- | --------------------------------------------------------------------------- |
| `is_generating`     | Provider is currently processing an audio job                               |
| `is_generating_llm` | Provider is currently processing an LLM inference job                       |
| `is_disposable`     | Provider is available to be selected for a test (`false` = locked for test) |

Before any verification request is sent, the server:

1. Waits in parallel (up to `WAIT_FOR_FREE_TIMEOUT`) for all selected providers to finish their current job
2. Locks each free provider atomically (`is_disposable = false`) to prevent a production job from sneaking in
3. Fires all verification requests in parallel via `asyncio.gather`
4. Unlocks all providers in a `finally` block regardless of outcome

Production job dispatch (`_find_available_provider`) filters on `is_disposable = true` and both generating flags at `false`, so a provider under test or busy is invisible to the job queue.

##### How audio verification works

1. A random subset of active, non-trusted providers is selected (default: 30% of the pool)
2. A reference fingerprint is drawn randomly from the sample bank
3. Each selected provider receives `POST /verify` with `{ prompt, seed, duration }`
4. The server computes a **mel spectrogram fingerprint** (128 mel bands, averaged over time) for each returned WAV
5. Each fingerprint is compared to the reference via **cosine similarity**
6. A provider running a **different model** than the reference is automatically skipped (no penalty)

##### Scoring and banning

- A **pass** (`similarity ≥ SIMILARITY_THRESHOLD`) resets the provider's consecutive failure counter
- A **fail** increments `verification_failures`
- After **`MAX_CONSECUTIVE_FAILS` consecutive failures**, the provider is **automatically banned**
- A provider that fails to respond counts as a failed verification
- A provider busy beyond `WAIT_FOR_FREE_TIMEOUT` is **skipped for this round** with no penalty

##### Timing

Rounds run on a **random interval between 1h and 5h**. The randomness prevents providers from predicting when the next check will occur.

##### Results

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

#### LLM verification

LLM inference requests sent to providers are verified on two levels:

**1. Conversation echo** — the provider must return the full conversation (system prompt + history + user message) verbatim alongside its response. Any mismatch between what was sent and what was echoed back → **immediate ban**.

**2. Semantic similarity** (warn only) — the central server embeds the user message and the provider's response using `nomic-embed-text` locally and computes cosine similarity. A score below `LLM_COSINE_THRESHOLD` does **not** trigger a ban, but is logged in `provider_semantic_warnings` with the full prompt, response, and score. This table allows the operator to identify providers that systematically return semantically inconsistent responses over time.

```
provider_semantic_warnings
├── provider_id
├── user_message      (truncated to 2000 chars)
├── llm_response      (truncated to 2000 chars)
├── similarity_score
├── threshold
└── created_at
```

---

### Security

#### General

- **FFmpeg validation**: every WAV received from a provider is validated (format, duration 1-60s, max size 50MB) before being sent to the client — immediate ban if invalid
- **Audio proof-of-work**: periodic mel fingerprint comparison against an encrypted reference bank ensures providers run a genuine audio model
- **LLM conversation echo**: providers must return the exact conversation they received, string-to-string — immediate ban on mismatch
- **LLM semantic logging**: cosine similarity between user prompt and response tracked per provider for long-term anomaly detection
- **Encrypted fingerprint bank**: reference fingerprints stored with a dedicated Fernet key — independent of other keys
- **Per-provider API key**: auto-generated (`op_` + 48 random characters)
- **Public ownership records**: use `public_user_id` (UUID, never the internal ID)
- **Authenticated heartbeat**: provider identifies itself via API key

#### Provider ↔ Central Server Communication Security

**Unified `/process` endpoint** — All provider operations route through a single `POST /process` endpoint with an `action` field. This minimizes attack surface and simplifies validation.

**Strict Pydantic schema validation**:

| Endpoint                         | Request                                                                                | Response                                                                                               | Notes                                |
| -------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------ |
| `POST /process?action=health`    | `{"action": "health"}`                                                                 | `ProviderHealthResponse`                                                                               | All fields mandatory, enum-validated |
| `POST /process?action=status`    | `{"action": "status"}`                                                                 | `ProviderStatusResponse` with `available`, `generating`, `generating_llm`, API key, model, VRAM        | All fields mandatory, enum-validated |
| `POST /process?action=generate`  | `{"action": "generate", "prompt", "duration", "seed"}`                                 | HTTP 200 with WAV body + headers: `X-Provider-Key`, `X-Model`, `X-Duration`, `X-Sample-Rate`, `X-Seed` | All headers validated                |
| `POST /process?action=llm_infer` | `{"action": "llm_infer", "system_prompt", "history", "user_message", "image_base64?"}` | `LLMInferResponse` with echo + response + model                                                        | Immediate ban on echo mismatch       |

**Request validation**:

- `action` is an enum: only `"health"`, `"status"`, `"generate"`, `"llm_infer"` accepted
- Extra fields rejected via `extra="forbid"` — any unknown field causes immediate rejection and ban
- `system_prompt` and `user_message` max 32 000 / 8 000 chars respectively
- `history` roles restricted to `"user"` and `"assistant"` only — `"system"` in history is rejected
- `image_base64` validated as valid base64, max 10MB decoded

**Response validation (Pydantic models)**:

```python
class ProviderStatusResponse(BaseModel):
    available: bool
    api_key: str = Field(..., min_length=48, max_length=64)
    model: SupportedModel         # Enum-validated
    model_id: SupportedModelId    # Enum-validated
    device: SupportedDevice       # Enum-validated: must be "cuda"
    generating: bool
    generating_llm: bool
    vram_total_gb: float = Field(..., ge=0, le=999999)
    vram_used_gb: float = Field(..., ge=0, le=999999)
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

class LLMInferResponse(BaseModel):
    system_prompt: str
    history: list[LLMConversationMessage]
    user_message: str
    response: str
    model: str    # must be "gemma4:e2b"
    provider_key: str
    model_config = ConfigDict(extra="forbid")
```

- Any field missing, wrong type, or out of range → `ValidationError` → provider **auto-banned**
- LLM model mismatch (anything other than `gemma4:e2b`) → provider **auto-banned**
- Echo mismatch (system_prompt, history, or user_message differ from what was sent) → provider **auto-banned**

**Heartbeat endpoint** (`POST /providers/heartbeat`):

- Accepts **ONLY** `True` as JSON body
- Any other payload → `400 Bad Request` + provider **auto-banned**

**WebSocket connection** (`/providers/connect`):

- Provider establishes connection with `X-Provider-Key` header (mandatory, validated)
- Provider **sends nothing** except auto-ping frames
- If provider sends any application-level message → **immediate ban**
- Server never sends messages to provider

**Canary testing** (`randomly_test_providers`):

- Runs as a background task, independently of the verification loop
- At random intervals (1h–6h), tests ~1/3 of active providers
- Each session draws a **random subset** of tests (4–10 regular + 4–8 canary), **shuffled together**
- Regular invalid requests cover: unknown actions, missing fields, extra fields, out-of-range duration/seed, empty prompt, invalid history roles, oversized payloads
- **Canary actions are randomly generated** (`secrets.token_hex(6)`) — unpredictable even with full source access. If any canary action is accepted → provider **immediately banned**
- Inter-test delays are randomised (0.5s–3s) to prevent timing fingerprinting

**Automatic banning on any protocol violation**:

- Invalid Pydantic schema → ban
- LLM model mismatch → ban
- Conversation echo mismatch → ban
- Invalid WAV returned → ban
- Unsolicited WebSocket message → ban
- Heartbeat payload != True → ban
- Canary action accepted → ban

All bans trigger:

1. Admin notification email with provider name, ID, and reason
2. Provider notification email with remediation guidance
3. Provider downgraded from "provider" tier to "free"
4. Provider marked `is_banned = True` and `is_active = False`

---

### Monthly Redistribution Eligibility

A provider is eligible if:

1. **uptime_score = 1.0** — two conditions must both be met for the previous month:
   - Worked **≥ 8h on at least 80% of their active days** in the month
   - Accumulated **≥ 80% of their total expected hours** for the period
   - Providers who joined mid-month are evaluated proportionally from their join date
   - Providers who joined on the last day of the month are excluded from that month's redistribution
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

The redistribution report is saved to the `finance_reports` table at each execution.

**Automatic redistribution runs via cron on the 1st of each month.**

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
