# ai_checkpoints/

Cross-session work log for AI-assisted development on this repo. The point is
continuity: any new session (or teammate) can read the latest checkpoint and know exactly
where things stand, what was decided, what's still open, and what to do next — without
replaying the whole conversation.

## What goes here vs. elsewhere

- **`ai_checkpoints/`** — point-in-time session/milestone snapshots. Mutable narrative:
  status, what got done, open questions, next steps. Read newest-first for current state.
- **`docs/adr/`** — permanent, numbered decision records. Once a question here is settled,
  promote the decision to an ADR; the checkpoint then just references it.
- **`docs/ARCHITECTURE.md`** — the durable target design (kept current as decisions land).

## Format

One file per session/milestone: `NNNN-slug.md`, zero-padded, increasing. Each contains:

- **Status** — one line (in progress / blocked / landed).
- **Done this session** — what changed, with enough detail to trust it.
- **Decisions** — settled choices (link the ADR once promoted).
- **Open questions** — what still needs the human, phrased so it's answerable.
- **Next** — the concrete next actions.

Keep them short. Link ADRs and files. Don't paste code.
