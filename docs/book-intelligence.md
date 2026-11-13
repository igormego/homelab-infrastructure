# Book Intelligence — planning handoff

Last reviewed: 2026-11-13.

**Status: Planned.** This records an agreed workflow, not an implementation or
Homelab deployment. No book importer, database, scheduled job or Goodreads write
integration has been verified by this workstream.

## Agreed boundary

- A curated Goodreads library remains the source of truth for books, shelves,
  ratings, reading status and ownership.
- A small secondary SQLite database is intended for cleanup, reconciliation,
  audit reports and possible future personal queries. PostgreSQL is not needed
  for the initial scope.
- Refresh from a user-initiated Goodreads CSV export every few months. Do not
  introduce scraping, automated Goodreads writes or a recurring sync service.
  This is the selected project policy, not a claim about current Goodreads API
  availability or terms.
- Cleanup means identifying missing historical books and series volumes,
  inconsistent shelf/category assignments and records requiring manual review.
  Findings are proposals; the user makes the authoritative Goodreads changes.

## Smallest useful implementation — not started

1. Inspect an actual export privately and preserve an immutable source copy.
2. Import to SQLite, preserving Goodreads book identity and original field
   values. Handle incomplete dates and ISBNs as text rather than inventing data.
3. Produce a read-only audit report with explicit uncertainty. A missing series
   volume is a candidate to investigate, not proof the user read or owns it.
4. Rebuild or refresh the secondary database from a later export; preserve
   provenance and keep any manual corrections separate from the raw source.

Language, schema, private storage path and packaging are still unselected.
A small CLI without a new Docker service is a candidate, not an accepted
deployment design. Reuse existing repository conventions where useful without
coupling this workflow to Movie or Game Intelligence.

## Privacy

Public GitHub may contain generic documentation, importer/schema code and
synthetic fixtures. Real exports, personal reading history, ratings, ownership
data, SQLite files and identifying account information remain private.
Do not publish the personal chat handoff or transfer unrelated media-service
permissions to this workstream.

## Ownership and next action

The Books chat owns reading discussion, cleanup priorities and this planning
note. No Book Intelligence implementation/state milestone is claimed, and no
new owned `.project/workstreams/*.yaml` file has been registered.
Do not edit the Core-owned root rollup `.project/state.yaml` for ordinary
discussion or overwrite Infrastructure / Media Automation state.

Next implementation task: read current [repository instructions](../AGENTS.md),
inspect the private export, then agree a minimal import/audit contract before
implementing it. Repository-only work needs no live Homelab connection.
Any later host installation or runtime acceptance requires a separately
authorized operational task in the appropriate existing environment.

See [current state](current-state.md) and [roadmap](roadmap.md) for other
workstreams. This plan is separate from their existing sync mechanisms.
