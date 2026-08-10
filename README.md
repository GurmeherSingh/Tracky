# Inbox-Driven Obligation Tracker

Turns Gmail into a live ledger of what I owe people — assessments, interview scheduling, offer
replies — and escalates each one until it's closed by evidence.

Built after I missed a real online assessment I had already read. The central object is an **open
obligation** that persists, escalates as its deadline approaches, and closes on evidence.

## Architecture

```
Gmail (readonly) → ingest (poll 2min, historyId cursor, date-ranged fallback)
                 → gates (0: server-side query · 1: sender tables · 2: bulk headers)
                 → classify (PURE: structured output, claude-sonnet-5 → opus-5 cascade)
                 → resolve (application matching; ambiguity → human review)
                 → state (append-only event log → derived status)
                 → obligations (create / close 4 ways / schedule alerts)
        Postgres (single source of truth)
                 → notify    (Slack: alerts, pinned board, digest, review queue)  [projection]
                 → sync      (Notion reconciler)                                  [projection]
                 → research  (web search → interview briefing on the Notion page) [projection]
                 → assistant (Tracky answers questions from these rows)           [read-only]
```

## How it's built

- **Postgres holds the truth.** Slack and Notion are one-directional projections; a projection
  failure never suppresses an alert.
- **Pending work lives in a nullable column** — `next_alert_at` for alerts, `briefed_at` for
  briefings. A null means still owed, so the next tick picks it up. Downtime becomes latency.
- **`tracker/classify/` is pure** — no DB, no network beyond the injected client, no clock. A test
  AST-parses the package for forbidden imports, which keeps classification replayable over stored
  email bodies.
- **Confidence thresholds run in opposite directions.** "Is this my application?" sends anything
  unsure to a Slack review queue. "Is this an obligation?" creates it anyway, labeled
  `unconfirmed`, and alerts.
- **Two Slack channels enforce alert hygiene.** `#job-alerts` (notifications on) carries only
  deadline tiers; `#job-tracker` (muted) carries board, digest, and review queue. The notifier
  class exposes no generic send method.
- **Gmail's message id is the primary key**, so every re-run and every overlap re-fetch is safe.
- **Briefings cite everything.** The prompt requires "Nothing found" for anything the model
  couldn't retrieve.

## Setup

Python 3.12+ and a Postgres database (a free [Neon](https://neon.tech) instance works).

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Windows (use bin/ on Linux/macOS)
copy .env.example .env                     # then fill in every value
.venv/Scripts/alembic upgrade head         # create the schema
```

**1. Google OAuth (Gmail read-only).** Cloud Console → enable the Gmail API → OAuth consent screen
(External, Testing) → OAuth client of type **Desktop app** → save as `credentials.json` in the repo
root. First run opens a consent screen and writes `token.json`. The scope is exactly
`gmail.readonly`.

**2. Slack app.** Enable **Socket Mode** (gives the `xapp-` token; no public endpoint) and
**Interactivity** (alert buttons, reminder modal).
Scopes: `chat:write`, `pins:write`, `reactions:read`, `app_mentions:read`, `im:history`, `im:read`,
`im:write`. Events: `reaction_added`, `app_mention`, `message.im`.
Create `#job-alerts` (notifications on) and `#job-tracker` (muted), invite the bot to both, and put
both channel ids in `.env`.

**3. Anthropic API key.** One key covers classification (`claude-sonnet-5`, cascading to
`claude-opus-5` on low confidence), Tracky (`claude-sonnet-5`), and briefings (`claude-opus-5` with
the server-side `web_search` tool).

**4. Notion (optional).** Internal integration, share a database, set its id. Properties needed:
`Name` (title), `Status` (select), `Company` (rich text), `Role` (rich text), `Source` (select),
`Last contact` (date), `Open obligations` (number). Leave the config unset and Notion sync and
briefings stay off.

## Running

```bash
python -m tracker.cli backfill --after 2026/06/01   # validation run over real mail
python -m tracker.cli run                            # the live system
python -m tracker.cli resync                         # force date-ranged re-ingest
python -m tracker.cli wipe                           # delete ALL data
```

`run` starts five guarded jobs plus the Socket Mode listener (buttons, reactions, Tracky):

| job | every | what it does |
|---|---|---|
| ingest + retry pass | 2 min | new mail → ledger; new applications get their Notion page |
| escalation sweep | 1 min | fires every alert whose `next_alert_at` has arrived |
| briefings | 10 min | researches companies with a pending interview |
| Notion reconcile | 5 min | re-projects every application's properties |
| digest check | 15 min | sends the 8am local digest once a day |

Every classification row is stamped with `prompt_version` and `model`, so comparing prompt
revisions is a SQL query.

## Failure handling

- Every job runs inside a guard, so the scheduler survives any exception.
- Logs are structured JSON to stdout and a rotating `logs/tracker.log` (2 MB × 3).
- A classification that fails transiently is re-attempted on the next ingest pass. Three failures
  park the email and post a Slack alarm pointing at the `failed_jobs` table.
- Gmail's `historyId` expires after about a week of downtime and returns 404. The pipeline falls
  back to a date-ranged resync with a 1-day overlap; idempotency makes the re-fetch free.
- Google's Testing-mode refresh token dies weekly. Any 401 posts a loud alarm to `#job-alerts`,
  with a 6-hour cooldown so it can't spam.
- Slack and Notion errors are caught per item, so one bad projection write can't roll back an
  ingest pass.
- Tracky retries once on an API error and then replies that it couldn't reach the model.

## How obligations work

- **Created** only from *actionable* email. "We'll be in touch" creates nothing.
- **Every obligation has a due date.** An unstated deadline becomes a 72h default, labeled
  `[assumed deadline]` in every alert.
- **Tiers:** detection → T-48h → T-12h → last-call, where last-call is `due − effort − 1h`
  (assessment 2h, take-home 4h, scheduling reply 5min, offer 1h, interview 30min). Alerts landing
  01:00–07:00 local move earlier.
- **A confirmed interview becomes its own obligation** with the meeting time as its deadline. Effort
  30min puts last-call 90min before the call. A stated time is required.
- **Closed four ways:** receipt email, next-stage email, rejection (→ `moot`), or the deadline
  passing (→ `unconfirmed_possibly_missed`). An elapsed interview closes as `elapsed`. A ✅ reaction
  closes manually.
- **Alerts carry Done / Remind me / Junk buttons** and an email deep link. "Remind me" opens a time
  picker and sets an off-ladder `reminder` tier, so escalation resumes afterward. Escalations thread
  under the first alert and broadcast to the channel.
- **The board shows what's still actionable.** Passed deadlines move to the digest. It's one pinned
  message edited in place, and it re-adopts itself from channel history if the pointer is lost.
- **Gone quiet:** a human replied, then nothing for 14 days → listed on the board without becoming
  an obligation.

## Interview briefings

An `interview_invite` event appends a sourced briefing to the company's Notion page: what they do,
what they say they value, recent news with dates, the published interview process, questions worth
asking, and a source list.

Research runs on `claude-opus-5` with Anthropic's server-side `web_search` tool, so citations come
back attached to the text and every claim on the page carries a clickable link. The model emits
markdown in a fixed section layout, which a tested converter turns into Notion blocks (`##`/`###`
headings, `- ` bullets, `[label](url)` links), appended 100 blocks at a time.

The job runs every 10 minutes over applications where `briefed_at IS NULL`, fanning out across a
thread pool for concurrent interviews. Every database read and write happens on the calling thread
and only plain tuples cross into workers, because a SQLAlchemy `Session` is not thread-safe.
`briefed_at` is stamped on success, so a failed run is simply picked up again.

On a live run the model wrote **"Nothing found"** under *Interview process* for a company whose
loop it couldn't source, and marked two funding items as headline-only.

## Asking it things

`@Tracky` in a channel or a DM — *"what's due this week?"*, *"where am I with Stripe?"*, *"remind me
about the take-home at 6"*, *"brief me on Stripe"*. It answers from three read tools over the ledger
plus `set_reminder` and `brief_company`, and receives links pre-formed as `<url|label>` so it pastes
them verbatim. `brief_company` reports back when a page has already been briefed.

## Known limitations

- Receipt-based closure is phrase-matching, so a missed receipt leaves the obligation open until
  next-stage or the deadline closes it.
- Company matching joins on the lowercased name, so "Stripe" and "Stripe, Inc." would split into two
  rows rather than merge.
- The Notion reconciler rewrites every row each cycle, so write volume scales with ledger size
  rather than with change.
- A briefing over 100 blocks appends in chunks; a failure between chunks would duplicate content on
  the retry. Real briefings run 40–70 blocks.
