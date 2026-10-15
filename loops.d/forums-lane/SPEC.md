# forums-lane — intake spec

1. Purpose & stop condition

Lurk public forums, keep the threads where a reply from Generalissimo would be on-topic for Maguyva, and tee up copy he posts himself. Per firing: collect, assess every candidate, draft at most five top-tier replies, and write `$OUT_DIR/loop-data.commit/batch.json` for the Growth Console pull. Cross-run: the only finding, `forums-lane:collect-failed`, resolves when a later run does not report it because at least one enabled adapter succeeded. The loop does not retire. Quality over volume. No karma farming.

2. Agentic pattern

Outer shape is Human-in-the-loop. The engine proposes a batch and a contract. A person posts, skips, or marks relevant in Growth Console. There is no iterate-until-success across firings. Inside one firing the engine reads the collector files, assesses, drafts, and runs emit once. It does not retry a failed adapter.

3. Type & data flow (precheck gathers vs engine interprets)

`type=agent`. `precheck.sh` is deterministic. It exits 0 with empty stdout when `FORUMS_LANE_ENABLED=0`, which the harness records as `skipped-precheck`. Otherwise it runs:

`cd "$MAGUYVA_REPO/growth-console"` then `PYTHONPATH=. "$HOME/.venvs/forums/bin/python" -m console.forums collect --forum all --out "$OUT_DIR/inputs"`.

The collector fetches `$GC_BASE/api/forums/config` and `/api/forums/seen` (3 tries, 15 s) and writes `candidates.json`, `config.json`, `source_stats.json`, `assess.md`, `draft.md`, and `persona.md`. Its stdout is empty when there are zero candidates and not every adapter failed (`collected N candidates` otherwise, plus `finding: forums-lane:collect-failed` when every enabled adapter failed). The precheck adds no digest lines, so that stdout stays the harness skip signal.

`LOOP_DATA_DIR` defaults to `$LOOPS_ROOT/state/loop-data` when unset. The collector appends `forums-lane/rules-cache`, which is the 7-day rules cache. The runner's own `LOOP_DATA_DIR` at promotion time is the per-loop directory and is not set during precheck.

The engine interprets. It reads the input files, assesses, drafts, and pipes `{"assessments":[...],"drafts":[...]}` to emit. Emit writes the batch. The engine does not re-collect.

4. Cadence

`daily:07:10` local. `LOOPS_EXPECT_TZ` on firstparty is `Asia/Manila`, so 07:10 is 07:10 Manila. It is not converted from US Eastern. Design section 11 picks this hour so the run does not share a clock hour with the legacy llm lane at 08:40. Both hosts are Manila. A missed calendar firing coalesces to one run at wake.

5. Scope & exclusions

In scope: Reddit and Hacker News candidates the collector returns, assessment, top-tier drafts, the batch file, and the one collect-failed finding.

Excluded: any post, vote, message, or submit on any forum. Any browser, CDP, or Playwright use. Unauthenticated reddit.com reads. Facebook groups. YouTube. `/reddit-config` edits. Pushing the batch into Growth Console (the warmer on llm pulls it). Installing the loop. The legacy lane stays as it is until cutover.

6. Guardrails verbatim

- "Nothing posts. No adapter, route, loop or probe submits, comments, votes, messages or edits on any forum." (forums-lane design, section 12)
- "No browser reads Reddit." The loop does not drive a browser.
- "Reddit reads go through two doors only: RedditGrow's API, or Reddit's OAuth API as u/maguyva-ai with scope=read." Those reads happen inside the collector, during precheck.
- "Everything is report/propose-only. No component ever commits, pushes, or mutates a project outside `$LOOPS_ROOT`." (docs/INTERFACES.md §0) Emit writes only the run directory, which is inside `$LOOPS_ROOT`.
- Kill switch: `FORUMS_LANE_ENABLED=0` in the loops `.env` (precheck exits skipped). `loopctl set-schedule forums-lane manual` is the schedule switch.

These lines are also in `prompt.md`. The permission axes enforce the engine half. The precheck is unsandboxed and is reviewed code, not model behavior.

7. Permission axes + justification

- `perm_fs_write=workdir` — `engine=codex` maps `report_only` to a read-only sandbox, so emit could not write `$OUT_DIR/loop-data.commit/batch.json`. Same justification as ads-google. `notes` in `loop.conf` satisfies dangerous-combo rule 5.
- `perm_network=none` — the precheck fetches. The engine must not reach the network. Rules 1, 3, and 7 are not approached.
- `perm_local_exec=allowlist` — one entry, `$HOME/.venvs/forums/bin/python -m console.forums emit:*`. It validates and writes the batch. It does not post. Codex cannot pattern-enforce the allowlist (INTERFACES §7.2); the entry is the declared policy, as on the ads loops. Rule 4: the entry is not a bare remote CLI and not a known mutating verb.
- `perm_remote_mutation=none` — nothing remote is changed. No `remote_mutation_justification`. Rule 2 is not approached.
- `credential_env` is unset. Rule 8 hard-fails any non-empty value. The five names the collector needs (`REDDITGROW_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`) are comments in `loop.conf` and keys in the loops `.env`. The runner exports `.env` into the precheck and strips it from the engine, which is stricter than credential passthrough.

8. Finding identity (what a finding IS + finding_id derivation rule)

A finding is "every enabled forum adapter failed this collect." `finding_id` is the fixed literal `forums-lane:collect-failed`. Emit it only when `source_stats.json` field `finding` is that same string. The collector sets the field only when every source whose `reason` is not `disabled` has `ok: false`. One disabled adapter, one empty feed, or a partial failure is not this finding. The id has no date, count, run id, or adapter name. See `prompt.md` section `## Finding identity`.

9. Tier-1 semantics (ok/warn/alert meaning)

`ok` — emit wrote the batch, `finding` is null, no enabled adapter failed, overflow is 0, and notices are empty. A run whose candidates are all `not_relevant` is still `ok`.

`warn` — emit succeeded and collection did not all-fail, but an enabled adapter failed, overflow is non-zero, or notices are non-empty (for example Growth Console was unreachable and the checkout config was used).

`alert` — `finding` is `forums-lane:collect-failed`, or emit exited non-zero. The collect-failed finding is severity `alert`, so the harness status override stays red. An emit failure with no collect-failed finding uses an empty findings array so the declared `alert` is what the dashboard shows.

The engine does not run at all when precheck stdout is empty (`skipped-precheck`). That is a clean zero-candidate day or the kill switch, not `ok`/`warn`/`alert` from the model.

10. Tier-2 metrics + panels

Inside the `metrics` JSON string, all numbers:

- `candidates` — `candidate_count`
- `sources.ok` — sources with `ok: true`
- `sources.failed` — sources with `ok: false` and `reason` other than `disabled`
- `notices` — notice count
- `drafts` — drafts piped to emit

`dashboard.json` declares a `number` panel on `candidates` (neutral), a `number` panel on `sources.failed` (`higher_is_worse`, warn at 1, alert at 3), and a 30-day `trend` on `drafts`. Panel color does not override `loop_status`.

11. Engine/model + budget

`engine=codex`, model blank (engine default). `timeout_s=900`. `retry_transient` stays at the default 1. A full day is at most about 40 candidates plus 5 drafts in one session (design section 11), so input tokens are in the tens of thousands when the title-gate cap is full, and a few thousand on a quiet day. Output is the assessments, the drafts, and the contract. The precheck stdout is one or two lines. The collector itself stops on its call budget and is expected to finish inside the precheck cap of 300 s.

12. Page output

None. Growth Console renders `/forums`. This loop does not ship a `render.sh`. The batch file is the handoff, not a report page.
