## Context

Firstparty runs the report-only weekly agent; llm owns existing search-service credentials. The current collector searches four GitHub repositories only. The operator requests Reddit and wider-web evidence with explicit origin and population limits, delivered through GC's existing validated importer.

## Goals / Non-Goals

Goals: deterministic, bounded and auditable multi-origin collection; no identity/credential export; truthful empty/error/selection metadata; unchanged model permissions and schedule.

Non-goals: a general search proxy, model-controlled queries, full-site crawls, new credentials/providers, email sends, statistical population estimates, or harness changes.

## Decisions

- Use a fixed-query `gtm-public-search` llm probe. Read only the existing Tavily configuration in memory and send its credential to the fixed official HTTPS endpoint. No credential is placed in argv, stdout, artifacts or firstparty environment. `--check` reads configuration only and never calls the API.
- Collect two Reddit-restricted and two wider-web queries with bounded results, response sizes and timeouts. Public JSON/RSS Reddit endpoints were unreliable in checks; use search-index excerpts and explicitly label that method instead of implying full-thread coverage.
- Merge probe output with GitHub results in trusted precheck, deduplicate URLs and retain each query's origin, method, window, limit and nonsecret failure category. Keep the final digest below the existing precheck cap.
- The agent interprets collected excerpts once and emits source-bound observations, optional document-level sentiment and positioning implications. GC computes counts from inputs, not model assertions. No pooled customer/public denominator.

## Risks / Trade-offs

- Search snippets omit context → display the method and excerpt; link to the original, avoid prevalence claims.
- Auth/quota/network failures → explicit per-query error; preserve other successful sources and last-good application output.
- Provider config changes → fail closed with a nonsecret category; never fall back to arbitrary config URLs or print exceptions containing credentials.

## Migration Plan

Hermetic tests and loop validation, reviewed matching probes on both hosts, supervised firstparty run, inspect GC's accepted source-family counts. Existing timers read updated loop definitions; no new schedule or permission grant is required.
