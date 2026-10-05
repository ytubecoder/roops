## Context

The fleet runs on firstparty and private intake lives on llm. GC already imports read-only firstparty outputs in its warmer. The two GTM loops produce ordinary validated contract metrics.

## Goals / Non-Goals

Goals: source-backed learning, quiet-input skips, reliable publication and weekly public evidence. Non-goals: harness changes, mailbox model access, email sending, changing approval or campaigns.

## Decisions

Keep all model permission axes at report_only/none/none/none. Deterministic prechecks gather bounded inputs; a single engine interprets them. Daily digest is supplied by a reviewed llm probe. Weekly GitHub issue queries are fixed and read-only. Store input artifacts beside contract.json so GC can validate provenance. GC pulls outputs; no push mutation permission is needed.

## Risks / Trade-offs

Public API rate limits → low query volume, per-source failures and bounded requests. Model hallucination → strict GC validation of source IDs and hash before publication. Failed runs → previous successful contract remains available. Sparse samples → explicit low confidence and response counts.

## Migration Plan

Validate both definitions; deploy probes on both hosts; run supervised firstparty jobs; inspect their reports against collected inputs; install timers only after success. Existing report/history remains recoverable.
