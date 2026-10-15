# forums-lane deploy (firstparty)

Operator recipe for the sparse checkout, the venv, and the loop. Do not install from a peon worktree. Install only after a supervised `loopctl run forums-lane` and the section 13 smoke in the forums-lane design.

The fleet host is firstparty, user `svc`, timezone `Asia/Manila` (`LOOPS_EXPECT_TZ`). The loop schedule `daily:07:10` is that local time.

## 1. Sparse checkout

On firstparty, as `svc`:

```bash
cd ~/projects
git clone --filter=blob:none --sparse https://github.com/ytubecoder/maguyva-marketing.git
cd maguyva-marketing
git sparse-checkout set growth-console/console growth-console/brands/maguyva
```

Two-checkout rule: push from llm, then `git pull` on firstparty before a supervised run or an install. The collector imports `console.forums` from this tree. It does not need the rest of the repo.

## 2. Venv

```bash
python3 -m venv ~/.venvs/forums
~/.venvs/forums/bin/pip install httpx pyyaml python-dotenv
```

The venv is not a full Growth Console install. `precheck.sh` sets `PYTHONPATH=.` from `~/projects/maguyva-marketing/growth-console`.

## 3. Loops .env

Edit `~/projects/loops/.env` on firstparty. `GC_BASE` is already set (`http://192.168.1.52:8787`). Add the checkout and the kill switch:

```bash
MAGUYVA_REPO=$HOME/projects/maguyva-marketing
FORUMS_LANE_ENABLED=1
```

Add the collector key names. Values stay in this file. Do not commit them. Do not put them in `credential_env` (`loopctl validate` rule 8 hard-fails a non-empty value). The runner exports `.env` into the precheck and strips it from the engine.

```bash
REDDITGROW_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USERNAME=
REDDIT_PASSWORD=
```

`reddit-api` ships disabled until those four Reddit keys exist. An empty `REDDITGROW_API_KEY` makes that adapter report not-ok. A day when every enabled adapter fails raises finding `forums-lane:collect-failed`.

Kill switch without uninstalling: set `FORUMS_LANE_ENABLED=0`. The precheck exits 0 with empty stdout (`skipped-precheck`).

## 4. Rules cache

`precheck.sh` sets `LOOP_DATA_DIR` to `$LOOPS_ROOT/state/loop-data` when the variable is unset. The collector writes the 7-day rules cache at `$LOOP_DATA_DIR/forums-lane/rules-cache`. After a promoted run, the batch file is `$LOOPS_ROOT/state/loop-data/forums-lane/batch.json`. Do not point `LOOP_DATA_DIR` at the per-loop directory during precheck. The collector appends `forums-lane/rules-cache` itself.

## 5. Validate

```bash
cd ~/projects/loops
git pull
bin/loopctl validate forums-lane
bin/loopctl requirements forums-lane
```

`requirements` must show `env:GC_BASE`, `bin:curl`, and `env:MAGUYVA_REPO` met. Fix `.env` before a run when one is unmet.

## 6. Supervised run

```bash
cd ~/projects/loops
bin/loopctl run forums-lane
```

Read the engine transcript and `state/runs/<id>/`. Confirm:

- `inputs/candidates.json`, `inputs/assess.md`, `inputs/draft.md`, and `inputs/persona.md` exist when the run did not skip.
- Empty stdout from the collector became `runner_status=skipped-precheck` and the engine did not start.
- A non-empty collect produced `loop-data.commit/batch.json` in the run dir, then `state/loop-data/forums-lane/batch.json` after promotion.
- The engine ran only `$HOME/.venvs/forums/bin/python -m console.forums emit`.
- No forum write happened. No browser ran.

Then do the section 13 smoke from the forums-lane design (one real `reddit-api` call once credentials exist, and the Growth Console walk on llm). That smoke is not this recipe.

## 7. Install

Only after step 6 and the section 13 smoke:

```bash
cd ~/projects/loops
bin/loopctl install forums-lane
```

A peon does not run this step.

Schedule switch, if the loop must stop without an uninstall: `bin/loopctl set-schedule forums-lane manual`.
