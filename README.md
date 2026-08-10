# Inbox-Driven Obligation Tracker

Turns Gmail into a live ledger of what I owe people — assessments to finish, interviews to
schedule, offers to answer — and escalates until each one is closed by evidence.

I missed a real online assessment. I'd read the email; awareness just decayed. The failure mode
isn't ignorance, it's forgetting — so the central object here isn't an email or a notification but
an **open obligation** that persists and escalates.

## Architecture

```
Gmail (readonly) → ingest (poll 2min, historyId cursor, date-ranged fallback)
                 → gates (0: server-side query · 1: sender tables · 2: bulk headers)
                 → classify (PURE: structured output, claude-sonnet-5 → opus-5 cascade)
                 → resolve (ambiguity → human review, never auto-merge)
                 → state (append-only event log → derived status)
                 → obligations (create / close 4 ways / schedule alerts)
        Postgres (single source of truth)
                 → notify    (Slack: alerts, pinned board, digest, review queue)  [projection]
                 → sync      (Notion reconciler)                                  [projection]
                 → research  (web search → interview briefing on the Notion page) [projection]
                 → assistant (Tracky answers questions from these rows)           [read-only]
```

## Design rules

- **Postgres is truth; Slack and Notion are one-directional projections.** Notion downtime can
  never suppress an alert.
- **Pending work is a nullable column, not a queue** — `next_alert_at`, `briefed_at`. Null means
  still owed, so the retry path is *the absence of code*: the next tick sees the null again.
  Downtime degrades to latency, never loss.
- **`tracker/classify/` is pure** — no DB, no network beyond the injected client, no clock. A test
  AST-parses the package for forbidden imports.
- **Inverted confidence thresholds.** "Is this my application?" favors precision (unsure → review
  queue, never silently written). "Is this an obligation?" favors recall (unsure → create it,
  labeled `unconfirmed`). A wrong row pollutes forever; a missed obligation is the incident that
  started this.
- **Two Slack channels as a structural guarantee.** `#job-alerts` (notifications on, deadline
  tiers only) and `#job-tracker` (muted: board, digest, review). The notifier class has no generic
  send method, so alerts *cannot* go anywhere else.
- **Idempotent everywhere.** Gmail's message id is the primary key; every re-run and overlap
  re-fetch is safe by construction.
- **No claim without a source.** Briefings must write "Nothing found" rather than assert anything
  the model couldn't retrieve.

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
root. First run opens a consent screen and writes `token.json`. Scope is exactly `gmail.readonly` —
the token physically cannot send, delete, or label mail.

**2. Slack app.** Enable **Socket Mode** (gives the `xapp-` token; no public endpoint) and
**Interactivity** (alert buttons, reminder modal).
Scopes: `chat:write`, `pins:write`, `reactions:read`, `app_mentions:read`, `im:history`, `im:read`,
`im:write`. Events: `reaction_added`, `app_mention`, `message.im`.
Create `#job-alerts` (notifications on) and `#job-tracker` (muted), invite the bot to both, put
both channel ids in `.env`.

**3. Anthropic API key** — one key covers classification (`claude-sonnet-5`, cascading to
`claude-opus-5` on low confidence), Tracky (`claude-sonnet-5`), and briefings (`claude-opus-5` with
the server-side `web_search` tool — no separate search key or dependency).

**4. Notion (optional)** — internal integration, share a database, set its id. Properties needed:
`Name` (title), `Status` (select), `Company` (rich text), `Role` (rich text), `Source` (select),
`Last contact` (date), `Open obligations` (number). Briefings need no extra property — they append
page *content*, a different API surface. Unset → Notion sync and briefings don't run.

## Running

```bash
python -m tracker.cli backfill --after 2026/06/01   # validation run over real mail
python -m tracker.cli run                            # the live system
python -m tracker.cli resync                         # force date-ranged re-ingest
python -m tracker.cli wipe                           # delete ALL data
```

`run` starts five guarded jobs plus the Socket Mode listener (buttons, reactions, Tracky):

| job | every | why that interval |
|---|---|---|
| ingest + retry pass | 2 min | APScheduler *drops* overlapping runs, and a catch-up pass can outlast a 1-min tick |
| escalation sweep | 1 min | a user-set reminder should land near its chosen minute; the query is indexed |
| briefings | 10 min | research takes minutes; inside ingest it would stall email processing |
| Notion reconcile | 5 min | cheap self-repair of any missed projection write |
| digest check | 15 min | fires the 8am local digest once per day |

Every job survives any exception. Logs are JSON to stdout *and* a rotating `logs/tracker.log` —
stdout dies with the terminal, and overnight failures need the lines written before the restart.
A transient failure is retried on the next ingest pass; without that retry the 3-strike counter
could never advance, since a re-seen email short-circuits as a duplicate. Three strikes *parks*
the email and Slack says so — nothing downstream would ever mention it again.

Every classification row is stamped with `prompt_version` and `model`, so comparing prompt
revisions is a SQL query rather than a guess.

## How obligations work

- **Created** only from *actionable* email. "We'll be in touch" creates nothing.
- **No obligation without a due date.** Unstated → 72h default, labeled `[assumed deadline]` in
  every alert.
- **Tiers:** detection → T-48h → T-12h → last-call, where last-call is `due − effort − 1h`
  (assessment 2h, take-home 4h, scheduling reply 5min, offer 1h, interview 30min). Alerts landing
  01:00–07:00 local move *earlier*, never later.
- **A confirmed interview is its own obligation.** No reply is owed once a slot is booked, but it's
  still a place you have to be. Effort 30min puts last-call 90min before the call. Never created
  from an assumed time — a meeting time was stated or it wasn't.
- **Closed four ways:** receipt email, next-stage email (an interview invite closes the
  assessment), rejection (→ `moot`), or the deadline passing — which marks
  `unconfirmed_possibly_missed`, not "missed", because the system separates what it knows from what
  it suspects. An elapsed interview closes as `elapsed`: over, not overdue. ✅ closes manually.
- **Alerts are actionable** — Done / Remind me / Junk plus an email deep link. "Remind me" sets an
  off-ladder `reminder` tier, so escalation resumes afterward instead of being consumed.
  Escalations thread under the first alert and broadcast; plain replies would get *quieter* as the
  deadline approached.
- **The board is what you can still act on.** Passed deadlines move to the digest. It's one pinned
  message edited in place, and it re-adopts itself from channel history if the pointer is lost.
- **Gone quiet ≠ owed.** A human replied but nothing in 14 days → listed on the board, never an
  obligation. Waiting on someone else isn't work you owe.

## Interview briefings

An `interview_invite` event appends a sourced briefing to the company's Notion page: what they do,
stated values, dated recent news, the published interview process, questions to ask, sources.

- **Server-side `web_search`, not a search API.** The deciding factor is citations — every claim on
  the page carries a clickable link. An external search key plus fetch-and-parse is three more
  moving parts for the same output; model memory alone fabricates.
- **Markdown out, not structured JSON.** The two are incompatible in the API, and sources win —
  hence a small tested markdown→blocks converter. Its subset (`##`/`###`, `- `, `[label](url)`) and
  the prompt's formatting rules are one contract.
- **Own job, gated on `briefed_at IS NULL`.** Research fans out over a thread pool; every DB read
  and write stays on the calling thread, because a SQLAlchemy `Session` isn't thread-safe and only
  plain tuples cross the boundary.

Observed rather than aspirational: on a live run the model wrote **"Nothing found"** under
*Interview process* for one company instead of guessing at a loop.

## Asking it things

`@Tracky` in a channel or DM the bot — *"what's due this week?"*, *"where am I with Stripe?"*,
*"remind me about the take-home at 6"*, *"brief me on Stripe"*. It answers strictly from tool
results (three read tools plus `set_reminder` and `brief_company`) and receives pre-formed
`<url|label>` links so it pastes a token rather than building link syntax.

If the model is unreachable it retries once and then *says so* — silence reads as a broken bot.
`brief_company` refuses an already-briefed page: that retry wraps the whole conversation, so
without the guard a second pass would append the briefing twice.

## Known limitations

- Google OAuth Testing mode expires refresh tokens weekly. Self-alarmed on any 401; re-auth is one
  command.
- Gmail's `historyId` expires after ~a week of downtime and returns **404, not an empty list** →
  date-ranged resync with a deliberate 1-day overlap (idempotency makes the re-fetch free).
- Receipt closure is phrase-matching; a miss just leaves the obligation open until next-stage or
  deadline closes it.
- Company matching joins on the lowercased name, so "Stripe" vs "Stripe, Inc." splits rather than
  merges — safe, but a split. A normalization table was dropped: validation over real mail produced
  no duplicates.
- The Notion reconciler rewrites every row each cycle instead of diffing, so write volume scales
  with ledger size rather than change. Fine here, wrong at 10×.
- A briefing over 100 blocks appends in chunks; a mid-chunk failure duplicates content on retry.
  Real briefings run 40–70 blocks, so this is a gap rather than a handled case.
- `brief_company` holds its DB session open for the whole two-minute research call — fine for one
  user, wrong shape for concurrent ones.

## Roadmap

Calendar entries for deadlines · follow-up nudges drafted for approval · diff-before-write in the
Notion reconciler · re-briefing when a page goes stale · Gmail push via Pub/Sub (the poll is an
interface push can drop into).
