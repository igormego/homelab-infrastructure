# ADR 004: Explicit preservation-first movie reacquisition

- Date: 2026-10-20
- Status: Accepted for repository implementation; component-tested, not live-write-accepted
- Scope: Homelab/Media, My Cinema reacquisition v0.1

## Decision

Add a separate explicit `reacquire_movie` boundary for an existing Radarr movie
that is unmonitored and has no media file. Preserve ADR 003 and `request_movie`
behavior for ordinary new requests. This is a narrow exception to its Seerr-only
mutation gateway, not a bypass of `already_managed`.

Choose Radarr's single-target monitor-only movie editor followed by one
`MoviesSearch`, after fresh identity/availability/queue/request/policy checks.
The native Seerr existing-movie path also applies configured defaults (profile,
root, minimum availability and tags); it has a larger implicit mutation surface.
Avoid it rather than rewriting movie policy to match Seerr defaults. No Seerr
request record is created by this operation.

## Consequences

Every execution requires explicit opt-in and a reviewed plan fingerprint. A
private durable exclusive reservation prevents repeated execution across local
processes/restarts. Unknown results stop for reconciliation, never auto-retry.
Receipts are separate from the Watched Event Ledger and media.db. No production
deployment or reacquisition is authorized by the component-tested milestone.

The APIs offer no shared transaction with Plex, Seerr or external operators.
Serialize callers through the same canonical endpoint/journal, retain receipts,
and avoid competing manual requests during live acceptance. Monitoring may
remain enabled after a partial failure; reversal cannot undo a submitted search
or download. See the [contract, tests and live boundary](../../tools/movie-intelligence/REACQUIRE.md).
