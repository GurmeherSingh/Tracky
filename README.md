# Inbox-Driven Obligation Tracker

My inbox already knows everything about my job search. This system turns it into a live ledger of
what I owe people — assessments to finish, interviews to schedule, offers to answer — and refuses
to let me forget any of it.

The origin story: I missed a real online assessment. Not because I didn't get the email — I got
it, read it, and then awareness decayed. The failure mode isn't ignorance; it's forgetting. So the
central object here is not an email or a notification but an **open obligation** that persists,
escalates as its deadline approaches, and must be closed by evidence.

## Architecture

```
Gmail (readonly) → ingest (poll 5min, historyId cursor, date-ranged fallback)
                 → gates (0: server-side query · 1: sender tables · 2: bulk headers)
                 → classify (PURE: structured output via claude-sonnet-5,
                             low-confidence cascade to claude-opus-5)
                 → resolve (application matching; ambiguity → human review, never auto-merge)
                 → state (append-only event log → derived status)
                 → obligations (create / close via 4 automatic paths / schedule alerts)
        Postgres (single source of truth)
                 → notify (Slack: alerts, pinned board, daily digest, review queue)  [projection]
                 → sync   (Notion reconciler, diff-and-apply)                        [projection]
```

Design rules that shape everything:

- **Postgres is truth; Slack and Notion are one-directional projections.** Notion downtime can
  never suppress an alert.
- **Escalations are a polled `next_alert_at` column, never in-process timers** — pending alerts
  are rows, so crashes and redeploys can't lose them ("database as durable timer").
- **The classifier core (`tracker/classify/`) is pure** — no DB, no network beyond the injected
  API client, no clock. Enforced by a test that AST-parses the package for forbidden imports.
  This keeps classification deterministic to test and cheap to replay over stored email bodies.
- **Inverted confidence thresholds:** "is this my application?" favors precision (unsure → review
  queue, never silently written); "is this an obligation?" favors recall (unsure → create it and
  alert anyway, honestly labeled `unconfirmed`). A wrong DB row pollutes forever; a missed
  obligation is the incident that motivated the project.
- **Two Slack channels as a structural guarantee:** `#job-alerts` (notifications on; only
  deadline-driven tiers) and `#job-tracker` (muted; board, digest, review queue). The notifier
  class has no generic send method — alerts *cannot* go anywhere else.
- **Idempotency everywhere:** Gmail's message ID is the primary key; every re-run and every
  overlap re-fetch is safe by construction.

## Setup

Requires Python 3.12+ and a Postgres database (a free [Neon](https://neon.tech) instance works).

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Windows (use bin/ on Linux/macOS)
copy .env.example .env                     # then fill in every value
.venv/Scripts/alembic upgrade head         # create the schema
```

### Credentials

1. **Google OAuth (Gmail read-only).** Google Cloud Console → create a project → enable the
   Gmail API → OAuth consent screen (External, Testing) → create an OAuth client of type
   **Desktop app** → download as `credentials.json` into the repo root. First run opens a browser
   consent screen and writes `token.json`. The requested scope is exactly `gmail.readonly` —
   the token physically cannot send, delete, or label mail.
   *Known limitation:* in Testing mode Google expires refresh tokens after ~7 days. The system
   detects its own auth death (any 401) and posts a loud alarm to `#job-alerts` instead of
   silently ingesting nothing.
2. **Slack app.** Create an app → enable **Socket Mode** (generates the `xapp-` app token; no
   public endpoint needed) → bot token scopes: `chat:write`, `pins:write`, `reactions:read` →
   event subscriptions: `reaction_added` → enable Interactivity. Install to the workspace,
   create `#job-alerts` (notifications on) and `#job-tracker` (muted), invite the bot to both,
   and put both channel IDs in `.env`.
3. **Anthropic API key** for classification (`claude-sonnet-5`, escalating to `claude-opus-5`
   on low confidence).
4. **Notion (optional).** Create an internal integration, share a database with it, and set the
   database id. The database needs properties: `Name` (title), `Status` (select), `Company`
   (rich text), `Role` (rich text), `Source` (select), `Last contact` (date),
   `Open obligations` (number). If unset, Notion sync simply doesn't run.

## Running

```bash
python -m tracker.cli backfill --after 2026/06/01   # validation run over real mail
python -m tracker.cli run                            # scheduler + Socket Mode (the live system)
python -m tracker.cli resync                         # force date-ranged re-ingest
python -m tracker.cli wipe                           # delete ALL data (validation cleanup)
```

`run` starts four guarded jobs — ingest (5 min), escalation sweep (5 min), digest check (15 min),
Notion reconcile (15 min) — plus the Socket Mode listener for review-queue buttons and ✅/❌
reactions on alerts. Every job survives any exception; failures are logged as structured JSON and
poisoned emails land in a 3-strikes `failed_jobs` table surfaced by the daily digest.

## How obligations work

- **Created** only from *actionable* email ("here's your link, due Friday") — announced next
  steps ("we'll be in touch") create nothing.
- **No obligation exists without a due date.** Unstated deadline → conservative 72h default,
  honestly labeled `[assumed deadline]` in every alert.
- **Escalation tiers:** detection → T-48h → T-12h → last-call, where last-call is
  `deadline − effort estimate − 1h buffer` (effort by type: assessment 2h, take-home 4h,
  scheduling reply 5min, offer response 1h). Alerts landing in 01:00–07:00 local are pulled
  *earlier*, never later.
- **Closed automatically** four ways: platform receipt email ("thanks for completing"),
  next-stage email (an interview invite closes the assessment), rejection (open obligations
  become `moot`), or the deadline passing — which does *not* claim you missed it; it marks
  `unconfirmed_possibly_missed`, because the system distinguishes what it knows from what it
  suspects. A ✅ reaction closes manually; nothing ever waits on it.

## Instrumentation

Every classification row is stamped with `prompt_version` and `model`, so comparing prompt
revisions is a SQL query, not a guess. Live signals track alert precision (❌ reactions mark
junk), review-queue rate, and which of the four closure paths retired each obligation.

## Known limitations

- Google OAuth Testing mode: refresh tokens die weekly (self-alarmed, re-auth is one command).
- Gmail `historyId` expires after ~a week of downtime and returns **404, not an empty list**;
  the pipeline falls back to a date-ranged resync with a deliberate 1-day overlap (idempotency
  makes the re-fetch free) rather than risk a silent gap.
- Receipt-based closure is phrase-matching — a missed receipt just means the obligation stays
  open until next-stage/deadline closes it.
- Company matching joins on the lowercased extracted name. "Stripe" vs "Stripe, Inc." would
  create two rows (a safe split, never a wrong merge); a normalization table is a planned
  iteration if validation shows real duplicates.

## Roadmap

Calendar entries for deadlines · prep-doc generation on interview invites · follow-up nudges
drafted for approval · natural-language queries in Slack ("what's due this week?") · Gmail push
via Pub/Sub (the poll is an interface push can drop into).
