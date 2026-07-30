# PEON_REPORT

## Status

Complete. Implemented the final-review fix wave on branch `peon/rp-fixwave`.

## What changed

- C1: `loops.d/kagi-ban/precheck.sh` now pins `PATH` to the user's login-shell
  path before invoking `av`, with the required rationale comment and guarded
  fallback to inherited `PATH`.
- C2: `bin/redact.py` generic key/value redaction now uses
  `(?<![\w-])` so keyword tails inside compound identifiers such as
  `gh-cli-hosts-token` do not corrupt digest/finding lines. The tradeoff is
  documented above `_KV_RE`; specific high-value token patterns still run first.
- I1: `loops.d/kagi-ban/precheck.sh` now parses the current scan strictly and
  exits non-zero with `ERROR: current scan JSON unparseable: ...` on parse
  failure. Previous baselines remain tolerant and still read as `{}` when absent
  or corrupt.
- F1: `loops.d/kagi-ban/render_page.py` footer provenance now names
  `render_page.py` and `#report-data`. Regenerated
  `pagekit/reference/reference-page.html` with the specified command.

## Red-to-green evidence

- Added redaction tests for compound keyword lines, bare `token:`, bearer
  authorization, and digest-shaped precheck lines.
  Before the fix:
  `python3 tests/test_redact.py TestRedactPatterns.test_compound_keyword_finding_id_is_not_redacted TestRedactPatterns.test_digest_shaped_precheck_line_is_not_redacted`
  failed with both compound lines redacted.
  After the fix:
  `python3 tests/test_redact.py TestRedactPatterns.test_compound_keyword_finding_id_is_not_redacted TestRedactPatterns.test_bare_token_keyword_still_redacts_value TestRedactPatterns.test_authorization_bearer_still_redacts_value TestRedactPatterns.test_digest_shaped_precheck_line_is_not_redacted`
  passed, 4 tests OK.
- Added precheck corrupt-current-scan test.
  Before the fix:
  `python3 tests/test_kagi_ban.py KagiBanPrecheckTests.test_unparseable_current_scan_fails_without_committing_baseline`
  failed because precheck returned 0.
  After the fix: the same test passed, 1 test OK.
- Regenerated reference page:
  `python3 loops.d/kagi-ban/render_page.py pagekit/reference/fixture-scan.json --loop kagi-ban --run-id reference --host fixture --av-version 0.0-stub -o pagekit/reference/reference-page.html`
  wrote the page successfully.
- Envelope validation:
  `python3 bin/page_envelope.py check --file pagekit/reference/reference-page.html`
  exited 0.
- Full required suite:
  `bash tests/run-tests.sh` exited 0. Python unittest discover ran 339 tests OK;
  shell fixtures reported `test_adapters.sh` passed 158/0,
  `test_examples.sh` passed 35/0, `test_runner.sh` passed 115/0, and
  `test_runner_pages.sh` passed 19/0.

## Self-review

- Touched only the requested implementation, test, reference artifact, and this
  report file.
- Confirmed the C1 PATH change occurs before `av --version` and `av scan`.
- Confirmed current scan parse failure happens before `shutil.copyfile`, so
  `loop-data.commit/scan-prev.json` is not written on corrupt current JSON.
- Confirmed the generic redaction rule still catches bare `token:` and
  `Authorization:` lines.

## Open questions

None.

## Concerns

No remaining concerns. C1 intentionally has no hermetic test because it reads the
real login shell path, per task instruction.

## Revision Note

- Narrowed the C2 generic KV lookbehind to `(?<![A-Za-z0-9-])`, so hyphenated
  compounds and letter-adjacent tails still avoid generic redaction while
  underscore env-var compounds like `GITHUB_TOKEN=` keep value redaction.
- Added two regression tests: `GITHUB_TOKEN=hunter2` and `DB_PASSWORD=hunter2`
  redact their values.
- Verified `python3 -m unittest tests.test_redact tests.test_kagi_ban` and
  `bash tests/run-tests.sh` green.
