## Why

The GTM loop currently searches only GitHub issues. The operator requests wider-web and Reddit analysis separated from customer feedback with explicit provenance and sample/coverage limits.

## What Changes

- Add a reviewed llm public-search probe using the already configured Tavily service for fixed Reddit and wider-web searches; no credentials reach firstparty or the model.
- Merge bounded public results with existing GitHub collection, preserving source family, query, selection window, limits, dates and explicit failures.
- Require source-specific qualitative interpretation and disclose snippet/selection limitations; never claim surveyed customers or statistical representativeness.
- Preserve the weekly schedule, report-only model permissions and GC last-good validation.

## Capabilities

### New Capabilities

- `gtm-public-source-coverage`: Bounded, origin-labelled Reddit and wider-web collection for GTM research.

### Modified Capabilities

None in the main spec store; extends the unarchived B-32 loop definitions without changing harness contracts.

## Impact

`probes/gtm-public-search`, `loops.d/gtm-research/`, hermetic tests, matching llm/firstparty checkouts and GC's source-transparency importer. No harness, email, campaign or authentication-setting mutations.
