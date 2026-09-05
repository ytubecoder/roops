# 🛑 Retired warmstarts — DO NOT READ, DO NOT CITE

Everything in this directory is a **dead session handoff**. It existed only to
let a context be cleared and the next session pick the job back up. That job is
over.

**These files are not reference material.** They are kept solely so a future
question about how something came to be has an answer in git. Nothing may depend
on one. No live pointer — CLAUDE.md, code comment, ticket, or routing table —
may send a reader here; a historical log or plan that cited one at the time may
keep the citation, because that is what it said then.

They are wrong in ways that are hard to see:

- They state **live system state** — balances, PR status, spend, counts, what
  was running — that was stale within days of being written.
- Several are **internally contradictory**, having been corrected in one section
  and not another.
- Several are **superseded by a decision recorded elsewhere**, so acting on them
  re-opens something already settled.

Everything durable they contained was moved out before they were archived, on
2026-09-06: into `CLAUDE.md`, `README.md`, `PRODUCT_BACKLOG.md` (B-26/B-27/B-28),
`docs/KAGAMI_SETUP.md`, `docs/OPEN_THREADS.md`, `docs/LOOP_SELECTION.md`, and a
comment in `probes/av-scan`. If you are looking for how something works, that is
where it is.

If you need to know what one of these said, read it in git history — deliberately,
knowing it is a snapshot of a moment, not a description of the system.

**The convention that replaced them:** every doc here is either static
(maintained, and carrying no live system state — write the query, not the answer)
or ephemeral (write it, use it, delete it; keep it in the scratchpad, not the
repo). A doc with a "current status" or a "next steps" list is the second kind.
