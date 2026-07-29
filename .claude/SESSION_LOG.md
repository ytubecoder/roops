# Session Log

## 2026-07-30 — Repo created: brand exploration → landing site → full UI concept

### Summary
- Built the whole repo in one arc: brand exploration (washi/sumi/vermillion system, ensō mark, hanko seals) → public landing page → "The Garden" UI concept with engawa live drop-in, pancaked findings, 掃 swept pile, 帳 metrics ledger, tokonoma output alcoves, per-run token costs, and per-loop off switches. Live on GitHub Pages from the first day.
- Implementation split: design direction held in the main session (Fable); two big ui.html builds delegated to design subagents (fork for the first build; Opus designer agent for the pancaking/ledger and tokonoma reworks) with mandatory self-verification via Playwright over throwaway localhost servers.

### Lessons Learned
- **Gotcha:** Yuji Syuku (brush/seal face) collapses into unreadable blobs below ~16px — hit independently by two different agents. Rule promoted to CLAUDE.md: seal face for stamps only, small kanji in Mincho.
- **Gotcha:** the scroll-reveal pattern (`.reveal`, opacity 0 until IntersectionObserver fires) makes below-the-fold element screenshots come out blank — force `.in` classes before capturing.
- **Accepted:** subagent briefs that carry the full design-token list, hard bans, and a mandatory verify-then-cleanup protocol (serve → Playwright → screenshot → close browser → kill server → no git) — both Opus agents returned verified work and clean environments, including catching their own bugs (a `.sw` class collision, a seal-impression overlap).
- **Rejected:** run-history sparklines in the garden rows — user read them as "stage progress"; replaced by tokonoma output alcoves, trends moved to the ledger zone.
- **User correction (load-bearing, copy-level):** never describe Roops as flatly "report-only / never acts" — the accurate model is rounds-read-only + findings-are-actions-in-waiting + 承 approval → separate audited execution path. Grounded in the loops repo's OPEN_THREADS (approve→action bridge, ack ≠ approval).

### Decisions
- Separate public repo instead of Pages on the loops repo — keeps the harness untouched while the rebrand is a candidate; deleting one repo undoes everything.
- Public mock data is genericized (tls-certs, deps-drift…) — internal `ads-*` loop names stay off the public site; the internal-flavored variant went to the user via Taildrop only.
- Garden row anatomy fixed by user direction: [stamp | name | tokonoma | run meta w/ tok+$ | off switch]; severity column dropped (stamp carries it).
- Harness-side follow-ups surfaced by this design work (not built): timestamped `metrics` ledger table in the existing sqlite; pancake/stale semantics on finding identity; read-only SSE tail for a real engawa. All would be explicit INTERFACES amendments.
