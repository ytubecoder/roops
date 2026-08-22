# B-25 council review — consolidated (codex, grok, antigravity), 2026-08-23

Round 1 was run on the first draft of `design.md`; the revised draft incorporates the items below. Round 2 reviewed the revision.

## Accepted (spec revised)
- `ticket-add` I/O: one base64url-JSON arg carrying `{project,title,section,priority,description}`, ideas-only, description never logged — the first draft's arg grammar could not carry titles with spaces and omitted the `[loop:gc-actions | id]` description that is the idempotency key (all three).
- `.env` must not reach the engine env; own upper-case key regex (not `parse()`); load after `start-run` so a malformed file is a recorded `harness-error`; `.gitignore` (grok, codex).
- `probe:` requirement: live check at install/`requirements` (cached ping+list per process), config-only at run so the precheck's exit-75 path stays reachable; each probe implements `--check` so local mode is not vacuous (antigravity, codex, grok).
- ssh `--` must precede the host; `ping`/`list`/`check` are server built-ins; ssh_config stanza + `ssh-keyscan` printed by `keygen`; `--authorize --replace`; per-probe header grammar incl. `probe-timeout-s`; `av-scan` exports the login PATH before scanning; server prunes its own log (codex, grok).
- tar probes: relative paths, no links, `tarfile` data filter, bounded, written via `--out` because of the 64 KiB precheck stdout cap (codex, grok, antigravity).
- Two-checkout deploy rule; server-list drift = unmet requirement (grok).
- `ads-x-ledger` returns all batches; the sqlite snapshot peek is deleted explicitly (grok).
- `gc-actions` always via `bin/probe`; kagami zsh guard + `leak-terms.local.txt` copy; `bin:curl` wherever curl is used (grok, codex, antigravity).
- Dashboard mirror honours `LOOPS_INSTALL_BACKEND`/`LOOPS_SYSTEMD_UNIT_DIR`; existing dashboard/console fixtures pin launchd; console unit singleton; XDG default in the systemctl subprocess; TMPDIR at the top of the runner; console self-verify via `/api/state` within 30 s (grok, antigravity, codex).
- `snapshot --force` semantics; staging deletes the guest's scratch db; flip preconditions incl. zero-installed-on-llm and the phoneapp decision; free the tailnet name before the guest binds it; WP order WP1 → WP2‖WP3 → WP4 → WP5 (codex, grok, antigravity).
- LAN `http://loops.home.arpa` vhost dropped (grok's trust-boundary point) — tailnet only (D7).

## Partially accepted
- `bin:gh` does not prove auth (grok, codex): true; spec says so and relies on the install self-verify run rather than inventing an auth kind.
- kagami's install-time run may open a PR (grok): it is the loop's daily job; accepted and stated in the runbook.
- phoneapp as a design blocker (codex): it blocks the **flip**, which is already held (D6); made an explicit precondition rather than a design change.

## Pushed back (verified)
- "`ytubecoder/roops` is private" (grok): `gh repo view` → PUBLIC on 2026-08-23; `git ls-remote` works unauthenticated from the guest. §8.3 keeps `git clone`.
- "`shasum` may be absent on Debian" (grok): present on the guest (`/usr/bin/shasum`, perl installed).

## Round 2 (on the revised draft)
- Antigravity: every round-1 critical/warning resolved; one note (base64 expansion vs the 8192-char arg cap) → decoded payload cap set to 6 KiB.
- Codex: nothing from round 1 open; four refinements accepted — strip every key *named* in `.env` (not only the ones the runner exported), `validate` is a live check and `--check` runs only the probe's self-test, `list` carries a per-probe content hash so same-name drift is an unmet requirement, `restore --force` replaces the managed paths (temp-dir + swap) rather than merging.
- Grok: withdrew its two wrong round-1 facts (repo visibility, `shasum`); no criticals; seven implementation pins accepted — `env -u` is a prefix on the adapter child only (render still sees `.env`), `--no-live` changes only the `probe:` kind, the size caps reconciled, kagi-ban's digest reads `scan.findings`, `ping`/`list`/`check` are reserved probe names + header parsed from leading lines only, phoneapp "move" needs the untracked tree not just the credential, Caddy passes `Host` through and `console install` reuses the linger/tz/XDG refusals.

**Verdict (all three): ready to cut tasks.** Unresolved disagreements: none.
