# gtm-synthesis — intake

## 1. Purpose & stop condition
Assess changed GTM evidence; each firing ends with a validated recommendation. A finding resolves when its evidence gap disappears.

## 2. Agentic pattern
Outer Human-in-the-loop; single-shot interpretation inside. Never automatic approval or retries across firings.

## 3. Type & data flow
Agent. Precheck refreshes bounded private snapshots with gtm-refresh and reads anonymized digest through gtm-learning-read. The engine emits assessment in metrics. GC pulls contract plus inputs and validates references before publication.

## 4. Cadence
Daily 10:15 host local time. Identical hashes skip the engine only when GC already accepted that hash.

## 5. Scope & exclusions
Anonymized responses, public evidence and positioning hypotheses. No raw mailbox, private identities, email sending, campaigns, or approval writes.

## 6. Guardrails
Report/propose-only. Never approve a proposition. Never expose identities or raw mailbox data. Loops run on firstparty; llm supplies data.

## 7. Permission axes + justification
report_only / none / none / none. Trusted gtm-refresh performs bounded local snapshot writes. The model has no application mutation or network authority.

## 8. Finding identity
One durable evidence gap: gtm-synthesis:<condition>; conditions documented in prompt. No volatile values.

## 9. Tier-1 semantics
ok useful assessment, warn evidence gap, alert unusable inputs. A skipped unchanged digest means no new interpretation was needed.

## 10. Tier-2 metrics + panels
assessment object in metrics JSON string; consumed by GC. No numeric panels.

## 11. Engine/model + budget
Codex default, one invocation, timeout 600 seconds, retry_transient default 1. Several thousand tokens per changed digest, zero on unchanged inputs.

## 12. Page output
None; product display is GC /gtm. Harness contract report remains available.
