# Project: Inbox-Driven Obligation Tracker (internship take-home)

## Teaching mode — explain everything (IMPORTANT)

This project is part of the user's internship assessment. They must be able to understand and
defend **every block of this codebase out loud** — in a demo video and possibly a live interview.
Therefore, whenever writing or modifying code in this repo:

1. **Explain block by block.** After writing a file (or a meaningful chunk of one), walk through
   what each part does in plain language — not a restatement of the syntax, but what role it plays
   in the system.
2. **Always give the "why over alternatives."** For every non-obvious decision (a library, a
   pattern, a schema choice, a query shape), name the realistic alternatives and say why this one
   won: e.g. "polled `next_alert_at` column instead of in-process timers because timers die with
   the process." If the decision came from the design spec, connect it back to the product reason.
3. **Explain unfamiliar concepts on first use.** SQLAlchemy sessions, Alembic migrations, Socket
   Mode, prompt caching, idempotency keys — a 2–3 sentence plain-language explainer the first time
   each appears, per the user's global concept-explainer rule.
4. **Depth over speed.** It is fine for a task to take longer because the explanation is thorough.
   A block the user can't explain is a liability in the interview, so do not skip explanation to
   save tokens.
5. **Checkpoint understanding.** At the end of each task, summarize the 2–3 things the user should
   be able to say about it on camera (the "if asked, say this" version).

## Project context

- Design spec: `docs/superpowers/specs/2026-08-06-inbox-obligation-tracker-design.md`
- Implementation plan (18 TDD tasks): `docs/superpowers/plans/2026-08-06-inbox-obligation-tracker.md`
- Both are **local-only — never `git add` or commit anything under `docs/superpowers/`**.
- Postgres is the source of truth; Slack and Notion are one-directional projections.
- `tracker/classify/` must stay pure (no DB, no network, no clock) — the eval harness depends on it.
- All timestamps timezone-aware UTC. Naive datetimes are bugs.
- Gmail scope is exactly `gmail.readonly`; nothing is ever sent on the user's behalf.
- No fabricated data anywhere, including the demo (P15).
