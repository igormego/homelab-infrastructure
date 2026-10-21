# ADR 005: Historical watched archive presentation

- Date: 2026-10-21
- Status: Accepted for bounded implementation and one authorized pilot
- Scope: My Cinema, Homelab/Media

Use a separate Plex movie library with tiny explicitly labeled synthetic MP4s to
present lifetime watched movies that no longer have playable local copies.
media.db owns historical watched evidence; Plex playback history is not rewritten.
Radarr owns file state and Plex Movies owns playable representation. The archive
library stays excluded from Seerr availability synchronization.

Start with one explicitly approved Heretic entry in the accepted Watched — Test
library. Reuse the Zodiac placeholder approach, retaining that existing item.
Provide one idempotent, explicit-execution operation and private identity manifests;
do not add a scheduler, frontend, reacquisition trigger or bulk synchronization.

Archive/playable transitions are a documented future contract only. Removal must
eventually target owned synthetic files only, after verified real availability.
No automatic real-media deletion or Plex watched-state synchronization is allowed.

Contract, verification and rollback: [archive runbook](../../tools/movie-intelligence/ARCHIVE.md).
