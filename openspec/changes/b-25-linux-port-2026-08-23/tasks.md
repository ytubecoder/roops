# B-25 tasks — work packages (peon-dispatched, black-box acceptance)

Order: WP1 → (WP2 ‖ WP3) → WP4 → WP5 → foreman staging + rehearsal. Each WP is a self-contained
peon spec under `specs/`; the foreman records `--allow` + `--verify "bash tests/run-tests.sh"`
at dispatch and accepts on `peon check` + test audit + independent probes.

- [x] WP1 `specs/host-requirements/spec.md` — `.env` loader, `requires=`, `bin/requirements.py`, runner gating, TMPDIR
- [x] WP2 `specs/probe-channel/spec.md` — `bin/probe-server`, `bin/probe`, `bin/probe_core.py`, `probes/README.md`, `loopctl probe`
- [x] WP3 `specs/install-backend/spec.md` — backend-aware dashboard/console predicates, `loopctl console`, host checks, wording/docs
- [x] WP4 `specs/loop-retrofits/spec.md` — seven probes, kagi-ban/ads-x/gc-actions/tailnet-zones/kagami retrofits, `requires=` everywhere
- [x] WP5 `specs/state-snapshot/spec.md` — `loopctl snapshot`/`restore`, `workflows/firstparty-cutover.txt`, fleet docs
- [x] Foreman: stage the guest (git clone, `.env`, probe key + authorize on llm, `gh`), rehearsal in a rehearsal root (design §8.3)
- [x] Foreman: end-of-run questions batch (design §12); the flip waits for answers

Rehearsal 2026-08-23: every loop of the install set except kagami (no gh auth yet) ran `completed` on firstparty against a restored copy of llm's state; ads action sets written after installing bubblewrap + codex-code-mode-host. The flip (`workflows/firstparty-cutover.txt`) waits on the §12 answers.
