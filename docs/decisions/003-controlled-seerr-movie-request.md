# ADR 003: Controlled Seerr movie-request boundary

- Date: 2026-09-26
- Status: Accepted for repository implementation; component-tested, live write not accepted
- Scope: Issue #14, Infrastructure & Agent Control Plane

## Decision

Later scoped exception: [ADR 004](004-explicit-movie-reacquisition.md) introduces
a separate reacquisition operation for existing unmonitored/no-file movies;
this ADR's ordinary `request_movie` behavior remains unchanged.

Use Seerr as the sole mutation gateway for agent movie requests. Preserve Movie Intelligence and Radarr as read-only preflight sources and retain Seerr/Media Automation ownership of media policy. A controller returns plans by default and requires a call-level execution opt-in; no global write switch exists.

Resolve to a TMDb movie ID, fail closed on uncertain identity/source state, and refuse duplicates, existing managed titles, unavailable policy and TV requests. Send only the exact movie-request body through Seerr's configured defaults. After submission, verify request identity by read-back. Do not persist IDs, audit logs or settings inside this module.

## Replay tradeoff

Seerr 3.4.1 does not offer a transactional idempotency-key guarantee for this flow. Fresh preflight plus existing-request checks prevent ordinary replay; an in-process serialization guard and successful/uncertain receipts protect a controller lifetime. The orchestrator must serialize independent processes and preserve uncertain audit results across restarts. Unknown outcomes block resubmission until reconciled, rather than retrying a potentially successful POST. This scope does not claim distributed exactly-once delivery.

## Consequences

The public API exposes no arbitrary HTTP, SQL, shell, Docker or direct ARR mutation. Service/identity/API drift blocks rather than making policy choices. Admin request visibility and reviewed Seerr 3.4.1 semantics are required for v0.1. A later separately approved live issue must choose one movie and verify the complete flow; offline tests and read-only discovery do not establish live write acceptance.

Contract, allowlist, safety limits and evidence: [Seerr runbook](../../tools/movie-intelligence/SEERR.md).
