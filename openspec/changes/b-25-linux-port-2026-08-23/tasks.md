# B-25 tasks — work packages (peon-dispatched, black-box acceptance)

Order: WP1 → (WP2 ‖ WP3) → WP4 → WP5 → foreman staging + rehearsal. Each WP is a self-contained
peon spec under `specs/`; the foreman records `--allow` + `--verify "bash tests/run-tests.sh"`
at dispatch and accepts on `peon check` + test audit + independent probes.

- [ ] WP1 `specs/host-requirements/spec.md` — `.env` loader, `requires=`, `bin/requirements.py`, runner gating, TMPDIR
- [ ] WP2 `specs/probe-channel/spec.md` — `bin/probe-server`, `bin/probe`, `bin/probe_core.py`, `probes/README.md`, `loopctl probe`
- [ ] WP3 `specs/install-backend/spec.md` — backend-aware dashboard/console predicates, `loopctl console`, host checks, wording/docs
- [ ] WP4 `specs/loop-retrofits/spec.md` — seven probes, kagi-ban/ads-x/gc-actions/tailnet-zones/kagami retrofits, `requires=` everywhere
- [ ] WP5 `specs/state-snapshot/spec.md` — `loopctl snapshot`/`restore`, `workflows/firstparty-cutover.txt`, fleet docs
- [ ] Foreman: stage the guest (git clone, `.env`, probe key + authorize on llm, `gh`), rehearsal in a rehearsal root (design §8.3)
- [ ] Foreman: end-of-run questions batch (design §12); the flip waits for answers
