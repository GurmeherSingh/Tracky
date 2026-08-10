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
Gmail (readonly) → ingest (poll 2min, historyId cursor, date-ranged fallback)
                 → gates (0: server-side query · 1: sender tables · 2: bulk headers)
                 → classify (PURE: structured output via claude-sonnet-5,
                             low-confidence cascade to claude-opus-5)
                 → resolve (application matching; ambiguity → human review, never auto-merge)
                 → state (append-only event log → derived status)
                 → obligations (create / close via 4 automatic paths / schedule alerts)
        Postgres (single source of truth)
                 → notify    (Slack: alerts, pinned board, digest, review queue)  [projection]
                 → sync      (Notion reconciler, idempotent full write)           [projection]
                 → research  (web search → interview briefing on the Notion page) [projection]
                 → assistant (Tracky answers questions in Slack from these rows)  [read-only]
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
- **Pending work is a nullable column, not a queue.** `next_alert_at` for alerts, `briefed_at`
  for briefings — a null means "still owed," so the retry path for a failure is *the absence of
  code*: the next tick simply sees the null again. Downtime degrades to latency, never loss.
- **Nothing on a page without a source.** Briefings are written by a model with web search, and
  the prompt requires "Nothing found" over any claim it couldn't retrieve. Citations are the
  enforcement mechanism, not decoration.

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
   public endpoint needed) → enable Interactivity (needed for the alert buttons and the
   "Remind me" time-picker modal).
   Bot token scopes: `chat:write`, `pins:write`, `reactions:read`, `app_mentions:read`,
   `im:history`, `im:read`, `im:write`.
   Event subscriptions: `reaction_added`, `app_mention`, `message.im` — the last two are what let
   Tracky answer a mention in a channel and a direct message respectively.
   Install to the workspace, create `#job-alerts` (notifications on) and `#job-tracker` (muted),
   invite the bot to both, and put both channel IDs in `.env`.
3. **Anthropic API key.** One key covers all three uses: classification (`claude-sonnet-5`,
   escalating to `claude-opus-5` on low confidence), the Slack assistant (`claude-sonnet-5`),
   and interview briefings (`claude-opus-5` with the server-side `web_search` tool). Web search
   runs on Anthropic's infrastructure, so there is no separate search key or dependency.
4. **Notion (optional).** Create an internal integration, share a database with it, and set the
   database id. The database needs properties: `Name` (title), `Status` (select), `Company`
   (rich text), `Role` (rich text), `Source` (select), `Last contact` (date),
   `Open obligations` (number). Briefings need no extra property — they are appended as page
   *content*, which is a different API surface from the properties the reconciler writes. If
   unset, Notion sync and briefings simply don't run.

## Running

```bash
python -m tracker.cli backfill --after 2026/06/01   # validation run over real mail
python -m tracker.cli run                            # scheduler + Socket Mode (the live system)
python -m tracker.cli resync                         # force date-ranged re-ingest
python -m tracker.cli wipe                           # delete ALL data (validation cleanup)
```

`run` starts five guarded jobs plus the Socket Mode listener (review-queue buttons, alert
buttons, ✅/❌ reactions, and Tracky):

| job | every | what it does |
|---|---|---|
| ingest | 2 min | new mail → ledger; retry pass; new applications get their Notion page |
| escalation sweep | 1 min | fire every alert whose `next_alert_at` has arrived |
| briefings | 10 min | research companies with a pending interview |
| Notion reconcile | 5 min | re-project every application's properties |
| digest check | 15 min | send the 8am local daily digest once per day |

Intervals are not arbitrary. Ingest is 2 minutes rather than 1 because APScheduler *drops* an
overlapping run rather than queueing it, and a catch-up pass after downtime can outlast a
1-minute tick. The sweep is 1 minute so a user-chosen reminder lands near its chosen minute —
the `next_alert_at` query is indexed, so the extra ticks cost nothing. Briefings get their own
slow job because research takes minutes, and doing it inside ingest would stall email
processing behind company research.

Every job survives any exception. Failures are logged as structured JSON to both stdout and a
rotating `logs/tracker.log`, and an email that fails transiently is re-attempted on the next
ingest pass — without that retry the 3-strike counter could never advance, since a re-seen email
short-circuits as a duplicate. After three strikes the email is *parked* and Slack says so
explicitly: parking is the one failure a human must hear about, because nothing downstream will
ever mention it again.

## How obligations work

- **Created** only from *actionable* email ("here's your link, due Friday") — announced next
  steps ("we'll be in touch") create nothing.
- **No obligation exists without a due date.** Unstated deadline → conservative 72h default,
  honestly labeled `[assumed deadline]` in every alert.
- **Escalation tiers:** detection → T-48h → T-12h → last-call, where last-call is
  `deadline − effort estimate − 1h buffer` (effort by type: assessment 2h, take-home 4h,
  scheduling reply 5min, offer response 1h, interview 30min). Alerts landing in 01:00–07:00
  local are pulled *earlier*, never later.
- **A confirmed interview is its own obligation.** Once a slot is booked the classifier calls
  the email non-actionable — correctly, since no reply is owed — but a booked interview is still
  a place you have to be, so it becomes an `interview` obligation with the meeting time as its
  deadline. Its effort is 30 minutes, which puts last-call 90 minutes before the call starts:
  settling-in time, not doing time. A meeting time is never assumed — if no time was stated,
  nothing is created.
- **Closed automatically** four ways: platform receipt email ("thanks for completing"),
  next-stage email (an interview invite closes the assessment), rejection (open obligations
  become `moot`), or the deadline passing — which does *not* claim you missed it; it marks
  `unconfirmed_possibly_missed`, because the system distinguishes what it knows from what it
  suspects. An interview is the exception: a meeting whose time has passed is over, not overdue,
  so it closes quietly as `elapsed`. A ✅ reaction closes manually; nothing ever waits on it.
- **Alerts are actionable.** Each carries Done / Remind me / Junk buttons and a deep link to the
  source email. "Remind me" opens a time picker; the chosen time becomes an off-ladder
  `reminder` tier, so after it fires the normal escalation resumes from where it was rather than
  being consumed. Escalations for one obligation thread under its first alert and broadcast to
  the channel — plain thread replies would get *quieter* as a deadline approached.
- **The board is what you can still act on.** Passed deadlines leave the pinned board and live
  in the daily digest instead; bookkeeping shouldn't compete with work. The board is a single
  pinned message edited in place, and it re-adopts itself if the pointer is lost — on startup it
  scans recent channel history for its own prefix rather than posting a duplicate.
- **Gone quiet is tracked separately from owed.** An application where a human replied at some
  point but nothing has arrived in 14 days is listed on the board without becoming an obligation:
  waiting on someone else isn't work you owe, and it shouldn't generate a deadline. Offers and
  rejections are excluded — those are finished, not quiet.

## Interview briefings

When an `interview_invite` event lands, the company earns a briefing appended to its Notion page:
what they do, what they say they value, recent news and posts *with dates*, the interview process
if anyone has published it, questions worth asking back, and a source list. Research runs through
Anthropic's server-side `web_search` tool.

Three decisions worth naming:

- **Server-side search over a search API.** An external search key plus fetch-and-parse would be
  three more moving parts for the same output. The deciding factor was citations: the results
  arrive attached to the text, so every claim on the page carries a link you can click. Model
  knowledge alone was never an option — it fabricates, and a stale prior cannot know a company's
  current hiring loop.
- **Markdown out, not structured JSON.** Structured output is incompatible with citations in the
  API. Given the choice between a clean parse and real sources, sources win — hence the small
  tested markdown-to-blocks converter. Its supported subset (`##`/`###`, `- `, `[label](url)`)
  and the prompt's formatting rules are one contract; widening either alone renders literal
  markdown onto the page.
- **Its own job, gated on a null column.** `briefed_at IS NULL` means "owed a briefing," so a
  failed research call needs no recovery path. Research fans out over a thread pool for
  concurrent interviews, with every database read and write kept on the calling thread — a
  SQLAlchemy `Session` is not thread-safe, and only plain tuples cross the boundary.

The honesty rule is observable, not aspirational: on a real run the model wrote **"Nothing
found"** under *Interview process* for one company, explained that it could see two candidate-report
pages but could not open their contents, and marked two funding items as headline-only.

## Asking it things

`@Tracky` in a channel or DM the bot: *"what's due this week?"*, *"where am I with Stripe?"*,
*"remind me about the take-home at 6"*, *"brief me on Stripe"*. It answers strictly from tool
results over the ledger — three read tools plus `set_reminder` and `brief_company` — and is told
never to invent an application, deadline, or status. Tool results hand it pre-formed
`<url|label>` links so it pastes a token rather than constructing link syntax.

Two failure modes are handled deliberately. If the model is unreachable, Tracky retries once and
then *says so* — silence reads as a broken bot. And `brief_company` refuses an already-briefed
page: because the retry wraps the whole conversation, a side-effecting tool without that guard
would append the briefing twice.

## Instrumentation

Every classification row is stamped with `prompt_version` and `model`, so comparing prompt
revisions is a SQL query, not a guess. Live signals track alert precision (❌ reactions mark
junk), review-queue rate, and which of the four closure paths retired each obligation.

Logs go to stdout *and* `logs/tracker.log` (rotating, 2 MB × 3). The file matters: stdout dies
with the terminal, and diagnosing an overnight failure needs the lines written before the
restart. This was worth a test — `logging.basicConfig` alone was decorative here, because
structlog's default logger writes straight to stdout and no stdlib handler ever sees a line. The
test asserts on the file's contents, which is the only reason the bug surfaced.

## Known limitations

- Google OAuth Testing mode: refresh tokens die weekly (self-alarmed, re-auth is one command).
- Gmail `historyId` expires after ~a week of downtime and returns **404, not an empty list**;
  the pipeline falls back to a date-ranged resync with a deliberate 1-day overlap (idempotency
  makes the re-fetch free) rather than risk a silent gap.
- Receipt-based closure is phrase-matching — a missed receipt just means the obligation stays
  open until next-stage/deadline closes it.
- Company matching joins on the lowercased extracted name. "Stripe" vs "Stripe, Inc." would
  create two rows — a safe split, never a wrong merge. A normalization table was considered and
  dropped: validation over real mail produced no duplicates, so it would have been machinery for
  a problem this inbox doesn't have.
- The Notion reconciler rewrites every application's properties each cycle rather than diffing
  first, so its write volume scales with ledger size rather than with change. Harmless at this
  scale and wrong at ten times it.
- A briefing longer than 100 blocks is appended in chunks; if a chunk fails midway the retry
  starts from the top and that page gets duplicated content. Real briefings run 40–70 blocks, so
  this is a gap rather than a handled case.
- `brief_company` holds its database session open for the whole two-minute research call. Fine
  for one user; the wrong shape if this ever had concurrent ones.

## Roadmap

Calendar entries for deadlines · follow-up nudges drafted for approval · diff-before-write in the
Notion reconciler · re-briefing when a company's page goes stale · Gmail push via Pub/Sub (the
poll is an interface push can drop into).
