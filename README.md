# OBSIDIAN Neural — Distributed GPU Provider Network

> 🇫🇷 [Français](#français) · 🇬🇧 [English](#english)

---

## Français

### Vue d'ensemble

OBSIDIAN Neural fonctionne sur un réseau distribué de providers GPU. Chaque provider met à disposition son GPU pour générer des sons en temps réel. Les revenus des abonnements sont redistribués de manière **strictement égale** entre tous les providers éligibles chaque mois via Stripe Connect, après déduction d'une commission plateforme transparente couvrant les coûts d'infrastructure (fal.ai, hébergement, maintenance).

Si aucun provider n'est disponible, le système bascule automatiquement sur fal.ai (fallback).

---

### Architecture

```
Client VST
    ↓ POST /api/v1/generate
Serveur central FastAPI
    ↓ ping pool providers (aléatoire)
    ├── Provider disponible → génération locale
    │       ↓ validation FFmpeg
    │       ↓ écriture ownership JSON public
    │       ↓ retour WAV au client
    └── Aucun provider → fallback fal.ai
```

---

### Fichiers du système provider

| Fichier                                    | Rôle                                                   |
| ------------------------------------------ | ------------------------------------------------------ |
| `server/services/provider_service.py`      | Routing jobs, validation WAV, ownership JSON           |
| `server/services/provider_ping_service.py` | Pings aléatoires, uptime, redistribution Stripe        |
| `server/core/database.py`                  | Modèles DB : `Provider`, `ProviderJob`, `ProviderPing` |
| `server/api/routes/admin_providers.py`     | Routes admin CRUD providers + heartbeat                |
| `main.py`                                  | Scheduler de ping (toutes les heures)                  |
| `public/ownership.json`                    | Preuve de propriété publique des générations           |
| `public/finances.json`                     | Rapport financier mensuel public anonymisé             |

---

### Variables d'environnement (serveur central)

| Variable                   | Défaut | Description                                               |
| -------------------------- | ------ | --------------------------------------------------------- |
| `PLATFORM_FEE_PCT`         | `0.15` | Commission plateforme (15%) — couvre fal.ai + hébergement |
| `PING_PROBABILITY`         | `0.60` | Probabilité de ping à chaque heure                        |
| `MIN_UPTIME_SCORE`         | `0.60` | Uptime minimum pour être éligible                         |
| `MIN_BILLABLE_JOBS`        | `1`    | Jobs réels minimum dans le mois                           |
| `RANDOM_DELAY_MAX_MINUTES` | `50`   | Délai aléatoire max avant exécution du ping               |
| `PING_TIMEOUT`             | `5.0`  | Timeout du ping en secondes                               |

---

### Modèles de base de données

#### `Provider`

| Champ               | Type     | Description                                      |
| ------------------- | -------- | ------------------------------------------------ |
| `id`                | Integer  | ID interne                                       |
| `name`              | String   | Nom du provider                                  |
| `url`               | String   | URL du serveur d'inférence                       |
| `api_key`           | String   | Clé d'authentification (générée automatiquement) |
| `stripe_account_id` | String   | ID compte Stripe Connect                         |
| `is_active`         | Boolean  | Actif dans le pool                               |
| `is_banned`         | Boolean  | Banni définitivement                             |
| `ban_reason`        | String   | Raison du ban                                    |
| `jobs_done`         | Integer  | Total jobs traités                               |
| `jobs_failed`       | Integer  | Total jobs échoués                               |
| `billable_jobs`     | Integer  | Jobs réels (hors fallback fal.ai)                |
| `uptime_score`      | Float    | % de pings répondus ce mois (0.0 à 1.0)          |
| `last_ping`         | DateTime | Dernier ping envoyé                              |
| `last_seen`         | DateTime | Dernière réponse reçue (ping ou heartbeat)       |

#### `ProviderJob`

| Champ           | Type    | Description                                           |
| --------------- | ------- | ----------------------------------------------------- |
| `provider_id`   | Integer | Provider assigné (null si fallback)                   |
| `user_id`       | Integer | Utilisateur demandeur                                 |
| `status`        | String  | `pending`, `processing`, `done`, `failed`, `fallback` |
| `used_fallback` | Boolean | True si traité par fal.ai                             |

#### `ProviderPing`

| Champ              | Type     | Description            |
| ------------------ | -------- | ---------------------- |
| `provider_id`      | Integer  | Provider pingé         |
| `pinged_at`        | DateTime | Horodatage             |
| `responded`        | Boolean  | A répondu ou non       |
| `response_time_ms` | Integer  | Temps de réponse en ms |

---

### Système de ping aléatoire

Pour éviter qu'un provider triche en allumant son serveur uniquement au moment des pings prévisibles :

- Le scheduler se déclenche **toutes les heures**
- **60% de chance** que le ping soit effectivement envoyé (configurable via `PING_PROBABILITY`)
- **Délai aléatoire** de 0 à 50 minutes avant l'exécution (configurable via `RANDOM_DELAY_MAX_MINUTES`)
- Les pings sont loggés dans `provider_pings`
- L'`uptime_score` est recalculé après chaque vague

En parallèle, le provider envoie un **heartbeat** au serveur central toutes les 5 minutes, mettant à jour `last_seen` indépendamment des pings.

---

### Éligibilité à la redistribution mensuelle

Un provider est éligible si :

1. **uptime_score ≥ 60%** — il a répondu à au moins 60% des pings aléatoires du mois
2. **billable_jobs ≥ 1** — il a traité au moins 1 vrai job (hors fallback fal.ai) dans le mois

Si aucun provider n'est éligible, la redistribution n'a pas lieu.

---

### Redistribution Stripe

```
Revenus mensuels
    - Commission plateforme (15%) → couvre fal.ai + hébergement + maintenance
    = Montant distribuable
        ÷ nb providers éligibles
        = Part égale par provider
```

Exemple avec 180€ de revenus et 6 providers éligibles :

```
180€ - 27€ (15%) = 153€ distribuables → 25.50€ par provider
```

La commission est publiée dans `finances.json` à chaque redistribution — totale transparence.

**Déclencher la redistribution (admin) :**

```http
POST /api/v1/admin/providers/redistribution/compute
{
    "month_revenue_cents": 18000,
    "dry_run": true
}
```

Mettre `dry_run: false` pour déclencher les vrais transfers Stripe.

---

### Sécurité

- **Validation FFmpeg** : chaque WAV reçu d'un provider est validé (format, durée 1-60s, taille max 50 Mo) avant d'être transmis au client
- **Ban immédiat** si WAV invalide renvoyé
- **Clé API par provider** : générée automatiquement (`op_` + 48 caractères aléatoires)
- **LLM texte → fal.ai uniquement** : jamais chez les providers pour éviter les injections
- **Ownership JSON public** : preuve de propriété avec `public_user_id` (UUID, jamais l'ID interne)
- **Heartbeat authentifié** : le provider s'identifie via sa clé API

---

### Routes admin

| Méthode | Route                                            | Auth      | Description                  |
| ------- | ------------------------------------------------ | --------- | ---------------------------- |
| POST    | `/api/v1/admin/providers/heartbeat`              | API Key   | Heartbeat provider (interne) |
| GET     | `/api/v1/admin/providers/pool/status`            | ❌ public | Statut anonymisé du pool     |
| GET     | `/api/v1/admin/providers/`                       | Admin     | Liste tous les providers     |
| POST    | `/api/v1/admin/providers/`                       | Admin     | Ajouter un provider          |
| POST    | `/api/v1/admin/providers/redistribution/compute` | Admin     | Calcul redistribution        |
| GET     | `/api/v1/admin/providers/{id}`                   | Admin     | Détail + derniers jobs       |
| PATCH   | `/api/v1/admin/providers/{id}`                   | Admin     | Modifier un provider         |
| POST    | `/api/v1/admin/providers/{id}/activate`          | Admin     | Activer                      |
| POST    | `/api/v1/admin/providers/{id}/deactivate`        | Admin     | Désactiver                   |
| POST    | `/api/v1/admin/providers/{id}/ban`               | Admin     | Bannir                       |
| POST    | `/api/v1/admin/providers/{id}/unban`             | Admin     | Débannir                     |
| POST    | `/api/v1/admin/providers/{id}/regenerate-key`    | Admin     | Nouvelle clé API             |
| DELETE  | `/api/v1/admin/providers/{id}`                   | Admin     | Supprimer                    |
| GET     | `/api/v1/admin/providers/{id}/ping-stats`        | Admin     | Stats de ping                |

---

### Ajouter un provider

```http
POST /api/v1/admin/providers/
Authorization: Bearer <admin_token>
{
    "name": "Paul GPU",
    "url": "https://paul-provider.example.com",
    "stripe_account_id": "acct_xxxxxxxxxxxxx"
}
```

Réponse :

```json
{
  "message": "Provider added successfully",
  "provider": {
    "id": 1,
    "name": "Paul GPU",
    "api_key": "op_xxxxxxxx..."
  },
  "warning": "Save the api_key now — it will never be displayed again"
}
```

⚠️ La clé API n'est affichée **qu'une seule fois** — à transmettre immédiatement au provider.

---

### Kit provider

Le provider doit faire tourner un serveur d'inférence exposant ces endpoints :

```
GET  /status   → { "available": true, "model": "stable-audio-open-1.0", ... }
POST /generate → WAV bytes (Content-Type: audio/wav)
GET  /health   → { "status": "ok" }  (public, sans auth)
```

Le provider envoie aussi un heartbeat automatique :

```
POST /api/v1/providers/heartbeat  (toutes les 5 min, vers le serveur central)
```

Authentification via header `X-API-Key`. Configuration minimale GPU : **RTX 3070 ou équivalent**.

→ Voir le dépôt [obsidian-neural-provider](https://github.com/innermost47/obsidian-neural-provider) pour le kit complet.

---

### Fichiers publics

#### `public/ownership.json`

```json
[
  {
    "public_user_id": "550e8400-e29b-41d4-a716-446655440000",
    "provider": "Paul GPU",
    "prompt_hash": 1234567890,
    "duration": 10.24,
    "generated_at": "2026-03-28T14:32:00Z"
  }
]
```

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

### Statistiques publiques

Le serveur central expose en temps réel le nombre d'abonnés actifs :

```
GET https://api.obsidian-neural.com/api/v1/public/stats
```

Réponse :

```json
{
  "paying_users": 42,
  "updated_at": "2026-03-28T14:32:00Z"
}
```

Ces données sont publiques et non authentifiées. Elles permettent à chaque provider de vérifier la croissance de la plateforme et d'estimer sa part mensuelle.

Combiné avec `public/finances.json`, vous avez une vue complète et transparente de la santé financière du réseau :

| Source                   | Données                                   |
| ------------------------ | ----------------------------------------- |
| `/api/v1/public/stats`   | Nombre d'abonnés actifs en temps réel     |
| `/public/finances.json`  | Historique des redistributions mensuelles |
| `/public/ownership.json` | Preuve de propriété des générations       |

---

## English

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
