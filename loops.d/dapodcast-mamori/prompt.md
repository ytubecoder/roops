# dapodcast-mamori — prompt

You are invoked only when the backup precheck failed. Diagnose from the PRECHECK
OUTPUT block. Do not change files. Report one finding, `backup-failed:production`
(severity warn), whose detail quotes the failing line and names the likely cause:
- production API unreachable
- malformed state, refused so the good backup is kept
- git push or rebase failure
- missing node or clone

## Finding identity
`backup-failed:production`. Never put a timestamp or count in the id.

## Output contract
Your final message MUST be one JSON object conforming to `contract/contract.schema.json`:
- `schema_version`
- `run_id`: the RUN CONTEXT value
- `status`: `warn`
- `status_reason`: `backup_failed`
- `headline`
- `report_markdown`: at most 3 lines
- `metrics`: `"{}"`
- `findings`
