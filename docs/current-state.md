# Current State

## Reacquisition runtime preparation — 2026-10-21

**Verified:** accepted code deployed unchanged; existing Node 24.21.0 one-shot
runtime and private persistent journal prepared. Exclusive locking and persistence
across container restarts passed; the real operation journal is empty.

**Blocked:** none of the three proposed candidates passed strict preflight.
One Movies item lacks canonical TMDb/IMDb GUIDs, blocking absence verification;
Malignant also reports Available in Seerr despite no Radarr file. No live mutation
or acquisition occurred. Next: read-only identity/availability reconciliation,
then a fresh dry plan and separate live authorization. Do not bypass safeguards.
See the authoritative Media workstream state for retained evidence.


## My Cinema reacquisition v0.1 — component-tested — 2026-10-20

The separate [reacquisition controller](../tools/movie-intelligence/REACQUIRE.md)
plans by default, requires explicit execution plus a reviewed fingerprint, and
preserves existing movie policy using a monitor-only Radarr update followed by
one search. Durable receipts block concurrent/repeated or uncertain execution.
All 123 tests passed, including 60 unchanged existing tests. No live reacquisition,
deployment, download, monitoring change or Seerr POST was performed. Next: select
one qualifying non-Zodiac candidate and obtain separate live-test authorization.
Accepted Zodiac/Samsung and Seerr archive isolation layers remain unchanged.

## Kometa Phase 2A — runtime accepted; Trending correction component-tested — 2026-10-16

October 15 and 16 automatic 03:15 UTC runs completed successfully without overlap
or timeout. Daily picks were Inglourious Basterds → Margin Call → The Wailing;
Thursday Genre Spotlight rotated Thriller (12) → Drama (20). Scorsese remains the
only eligible director (4). October 16 counts are 2/3/1/4/20; ordering and existing
director, TV, overlay and native-setting regressions passed.

October 15 Trending fell to one item because minimum enforcement did not enable
deletion. Trending alone now uses `delete_below_minimum: true` with minimum 3;
all other retention semantics remain unchanged. The fix is deployed and 16 tests
passed, including pinned Kometa's actual minimum gates. No manual run or artificial
shortage was used. Natural below-minimum live deletion/recovery is an optional
remaining observation, not a claim of completed live acceptance for that branch.
See [policy and evidence](../tools/kometa/README.md). Earlier checkpoints below are historical.

## Kometa Phase 2A — Movies discovery accepted — 2026-10-14

Five ordinary collections are live-accepted: Newly Released (2), Trending Now (3),
Movie of the Day (1: Inglourious Basterds), Director Spotlight (4: Martin Scorsese),
and Genre Spotlight (12: Thriller). Sort titles are `!010_` through `!050_`.
Newly Released retains its existing smart collection and 90-day window. Trending
uses TMDb weekly top 100 intersected with local Movies, capped at 12 and minimum 3.

The accepted pinned 2.4.8 runner now generates only these five definitions. The
03:15 UTC schedule is unchanged. Daily selection is persisted for the Bratislava
calendar day; explicit director and genre pools use Monday and Thursday weekly
cadences, respectively. Only Scorsese currently meets the director minimum of 3;
the threshold is not weakened to manufacture rotation.

All pre-existing collection IDs and seven browsing directors were preserved,
along with native hub settings, TV collections and overlay labels. Eleven offline
tests and corrected live acceptance passed. No collection was omitted. Plex Pass
is **not** required for these collections; publishing was not retested.

See [policy, rotation and acceptance](../tools/kometa/README.md). Next: observe the
scheduled daily/Thursday rotation and gather feedback before another bounded milestone.

## Kometa Phase 1 — execution verified, Plex Pass prerequisite unmet — 2026-10-14

Historical Phase 1 checkpoint; Phase 2A above supersedes its single-row execution scope.

Kometa 2.4.8 now has a pinned, bounded daily one-shot job at 03:15 UTC using the
existing reboot-enabled cron service. The scheduled command is restricted to
Movies / `basic` / `Newly Released`. Its first manual invocation exited 0; the
first time-triggered invocation and reboot remain unobserved.

The previous run skipped this collection because no items met its minimum of one;
the fresh run applied the existing valid visibility configuration. Plex now stores
owner Home and Movies Recommended promotion, with shared Home disabled. However,
both hub-content endpoints omit the row. Plex reports `subscriptionActive=0`;
publishing collection rows requires an active Plex Pass. Both surfaces are
**unsupported under the current entitlement**, not a Kometa syntax failure.
Both members, all eight collections, director metadata/membership, native hub settings,
TV collections and private configuration were preserved. No overlays were run.

See the [execution and acceptance record](../tools/kometa/README.md). Next: check
the owner's decision on Plex Pass; if enabled later, repeat single-row visibility acceptance before adding rows.

## Media parking and ownership — 2026-10-07

Media is safely parked with the Mortgully Trakt delivery retained as an ambiguous blocker; no POST was issued during closure. Plex, Tautulli, Trakt, Seerr, Radarr/Sonarr, Movie Intelligence/media.db, media identity, watch history and media policy belong to the Homelab/Media domain. Personal AI Brain may consume bounded capabilities and own reusable delivery, cursor, permission, audit and adapter patterns, but it does not own Media-domain semantics. Public-indexer torrent cleanup is now accepted end-to-end.

## First ongoing Trakt delivery — SAFE CHECKPOINT after ambiguous POST — 2026-10-06

For Rick and Morty — *Mortgully: The Last Rickforest*, a durable `in_flight` delivery was created and **exactly one** Trakt history POST was attempted. The POST outcome could not be confirmed. Immediate item-specific read-back and a subsequent read-only reconciliation both found **0 matching history events**.

No Trakt history ID was captured, duplicate count is 0, and the delivery is durably marked `ambiguous`. Plex writes: 0. No unexpected DB changes; `PRAGMA quick_check = ok`.

**Do not retry automatically.** Next inspect only the sanitized original POST response/audit. If that evidence definitively proves no event was created (for example explicit `added: 0` / not-found semantics), one explicit retry can be considered separately. Otherwise leave the delivery ambiguous.

## First ongoing Trakt delivery — read-only blocker resolved, ready for write — 2026-10-06

The previous HTTP 403 blocker is resolved. The original Trakt client lacked an explicit `User-Agent`, and the failed refresh attempt used the legacy `api.trakt.tv` host instead of the current auth host. With the corrected request shape, a public API read and an OAuth-required read both returned HTTP 200; refresh was unnecessary because the existing access token remains valid.

The selected target, Rick and Morty — *Mortgully: The Last Rickforest*, now resolves unambiguously to the correct internal Trakt episode identity. Its item-specific history freshness check returned HTTP 200 with **0 events**, so the exact watch is absent. No `sync_deliveries` row exists yet and no Trakt POST has occurred.

**Next:** resume directly at the durable write boundary: create `in_flight`, issue exactly one history POST, immediately read back the exact event, capture its Trakt history ID as rollback evidence, and mark the delivery successful. Do not repeat earlier accepted observation or read-only diagnostics without new evidence.

## First ongoing Trakt delivery — SAFE CHECKPOINT on OAuth/API 403 — 2026-10-06

The first controlled `media.db -> Trakt` acceptance selected Rick and Morty, *Mortgully: The Last Rickforest*, but stopped safely before any delivery checkpoint or external write. Trakt returned HTTP 403 during read-only identity lookup, and the OAuth refresh request also returned HTTP 403.

No `sync_deliveries` row was created, no Trakt POST was attempted or could have occurred, and no Plex write occurred. Ledger state remains **3 watch_events / 3 external_event_links / 0 sync_deliveries**; `PRAGMA quick_check = ok` and no unexpected DB changes were observed.

**Resume exactly here:** diagnose and restore read-only Trakt OAuth/API access, then repeat only identity lookup + freshness check for the already-selected event. Do not repeat the accepted observation/reconciliation layer. Once read access works, create the durable in-flight delivery and continue with the already-authorized single POST -> exact read-back -> Trakt history ID capture.

## Tautulli reconciliation runner live acceptance PASS — 2026-10-06

**Verified PASS:** the deployed runtime tree matched merged `personal-ai-brain` commit `0285d235c55b5d1d73c9eaaa12ae487f484e7e10`. With no prior source cursor, the runner safely bootstrapped at `38`, inspected a bounded window and durably advanced to `42` after ingesting exactly two eligible Rick and Morty episode watches.

The two new canonical events preserved stable episode identity, exact real watched timestamps, Plex source, `tautulli_reconciliation` provenance and original Tautulli history IDs. A separate replay/re-entry process preserved cursor `42` and ledger counts at **3 watch_events / 3 external_event_links**, with one duplicate recognized and **zero duplicate inserts**. Trakt writes: 0. Plex writes: 0. No unexpected DB changes; `PRAGMA quick_check = ok`. A private backup was created before execution.

**Next watched-sync boundary:** one controlled real `media.db -> Trakt` delivery with freshness check, single write, read-back verification and rollback ID capture. Do not repeat further observation-only acceptance unless new evidence warrants it.

## Public ARR torrent one-minute cleanup policy — accepted end-to-end — 2026-10-14

Prowlarr 2.3.5.5327 now applies `torrentBaseSettings.seedTime = 1` minute with seed ratio left `null` to ten confirmed public torrent indexers: Bangumi Moe, dmhy, Knaben, LimeTorrents, nekoBT, Nyaa.si, SubsPlease, The Pirate Bay, TorrentDownload and YTS. Sk-CzTorrent remains unchanged because its ratio requirements are not yet known.

qBittorrent global ratio/time remain unlimited with action `Stop`; Sonarr/Radarr Completed Download Handling and removal remain enabled. No existing torrents or data were deleted while applying the policy, and a private rollback snapshot was created.

**Verified acceptance:** a fresh Radarr grab of *Scream 7* from public Prowlarr indexer Knaben received the policy. qBittorrent logged that the torrent reached its seeding-time limit after about 70 seconds. Radarr imported the movie, then requested torrent removal; qBittorrent removed the torrent job and download-side content. This closes the public-indexer cleanup acceptance loop. An older The Pitt S01E06 job still had unlimited limits, but its timing relative to policy application could not be established and is not evidence against the fresh accepted path.

## qBittorrent 5.2.4 zero-seed preflight — NO-GO — 2026-10-06

A read-only preflight evaluated LinuxServer qBittorrent `5.2.4_v2.0.15-ls479` as the candidate upgrade from `5.1.4`. General Sonarr/Radarr compatibility and rollback risk are acceptable, but the released 5.2.4 WebUI/WebAPI still does not reliably expose the category share-limit fields required to set `tv-sonarr` and `movies` to exact ratio `0.0` in this headless deployment. The upgrade would therefore not satisfy the reason for upgrading.

**Decision:** do not upgrade solely for ARR-category zero-seeding. Keep the current qBittorrent/Prowlarr configuration unchanged and revisit when a released build exposes stable category share-limit configuration through WebUI/WebAPI. No live or Git configuration changes were made by the preflight.

## Fresh Sonarr hardlink acceptance PASS — 2026-10-06

**Verified PASS:** The Pitt S01E04 (*10:00 A.M.*) imported successfully through Sonarr after the path-normalization fix. qBittorrent source and Sonarr library file are on the same device (`2049`), have the same inode (`256212996`), link count `2`, and exact size `8,008,236,700` bytes. This closes the fresh post-fix hardlink acceptance loop.

The torrent remains in qBittorrent for a separate reason: ARR Completed Download Handling and completed-torrent removal are enabled, but qBittorrent has unlimited global share ratio and seeding time. Its configured action is Stop only after a share limit is reached, so the torrent never becomes eligible for automatic ARR removal. No cleanup policy change has been applied yet.

## Tautulli reconciliation runner branch cleanup — 2026-10-06

The merged feature branch `feat/tautulli-reconciliation-runner` was deleted successfully. PR #10 remains merged and main remains intact.

## Tautulli reconciliation runner merged — 2026-10-06

**Verified:** PR #10 in `Az1mutt/personal-ai-brain` was squash-merged. `main` is verified at `0285d235c55b5d1d73c9eaaa12ae487f484e7e10`; required CI passed and the post-merge suite reports **118 passing tests**. Scope remained exactly six approved files, with clean diff/secrets checks and no live Plex/Trakt writes or deployment.

The restart-safe incremental reconciliation runner is now on main with durable cursor semantics. **Next gate:** bounded live observation-only acceptance against genuine recent watch events; Trakt/Plex writes remain disabled. Branch deletion was authorized but has not yet been independently confirmed complete.

## Watched Event Ledger live observation accepted — 2026-10-06

**Verified PASS:** Rick and Morty S09E06, *Erickerhead*, was observed as a real Plex/Tautulli watch at `2026-10-06T13:20:09Z`. Stable episode identity resolved across TMDb/IMDb/TVDB. The adapter inserted exactly one canonical `watch_event`, preserved exact timestamp/provenance and source event ID, and an immediate replay created no duplicate. No Trakt or Plex write was attempted.

The additive migration created the four ledger tables. Post-acceptance state is one `watch_event`, one `external_event_link`, zero deliveries and zero cursors; pre-existing `media.db` tables remained logically unchanged and a private backup was created.

**Next exact action:** implement a narrow restart-safe Tautulli reconciliation runner with a durable source cursor in a separate Change/Review PR. Keep the next phase observation-only; no Trakt/Plex writes.

## Sonarr hardlink path fix applied — 2026-10-06

**Verified live change:** the qBittorrent Remote Path Mapping now matches `/downloads/` and normalizes it to `/data/torrents/`. Sonarr's 50 series paths were rewritten to the common `/data/media/tv` root with `moveFiles=false`; no media move, container restart, or container recreation occurred. Read-back confirms the mapping and all 50 logical paths persisted.

**Regression check:** 11 series directories are absent through both the new path and the previous alias, with zero existence mismatches. All 475 recorded episode files were checked and zero are missing under their rewritten series paths. Existing library visibility is therefore preserved.

**Next acceptance:** one **future fresh post-fix torrent import** must still prove the fix end-to-end. PASS requires source/library same device, identical inode, matching size and link count >= 2 while qBittorrent retains the source. Pre-fix samples do not count as acceptance evidence.

## Watched-history Trakt backfill complete — 2026-10-06

**Verified:** all 558 watched movies are fully accounted in the Trakt historical pipeline. Bulk production, ambiguity resolution and the final Backrooms exception are closed. Backrooms was written with release date 2026-05-29 as explicit `legacy_placeholder` provenance after its retained Plex event could not be safely linked to the verified metadata item. The final write was read back successfully with zero duplicates or anomalies and private rollback evidence was captured.

Plex was not modified. Historical Plex watched-date backfill remains a separate unaccepted gate. `media.db` remains the internal durable data layer; Trakt is now the portable external history layer. Next media work should focus on ongoing synchronization/automation, Kometa iteration, or the remaining non-blocking ARR/Bazarr acceptance checks rather than repeating historical backfill.

## Stable identity enrichment and history preflight — 2026-10-05

Verified follow-up: all five previously excluded records now have confirmed TMDb and IMDb mappings, bringing the identity-qualified cohort to 40. Exact source metadata, credits and release history resolved festival/distribution year differences and rejected wrong-work hints. Existing external_ids was reused with a fresh SQLite-aware backup, disposable rehearsal, transactional invariant checks and reopened verification: 10 additive rows, no schema changes, no collisions, all pre-existing rows unchanged. A read-only history preflight qualified 39 proposals: two Plex-confirmed dates and 37 legacy placeholders; one unresolved Plex linkage is excluded. Zero cohort titles already exist in Trakt. All six previously accepted Trakt events remain unchanged and excluded. No Trakt/Plex/watch-date writes occurred.

The private 39-entry proposal is ready for separate review/authorization. Release-date placeholders are not genuine viewing dates; exact times are synthetic local noon. CSFD rating timestamps are never treated as viewing dates. Private identity/date/event evidence is not published.

## Whiplash completion — 2026-09-27 (Issue #17)

**Verified read-only at 19:04 UTC:** Whiplash (2014), TMDb `244786`, completed acquisition and import. Seerr request `2` is completed (status `5`). Radarr reports `has_file=true`, monitored=true and an empty matching download queue; existing default profile/root still match. Imported file `246` is Bluray-2160p, 23,434,594,215 bytes, added at `2026-09-27T15:49:03Z`. The file exists at the expected managed movie path and its disk size matches Radarr metadata.

Plex returned one matching Whiplash (2014) item with TMDb `244786` and a media part matching that exact imported file. **Plex visibility is verified; playback was not tested.** No request, ARR/download mutation, Plex scan/refresh, configuration, service or filesystem change was performed. This closes the pending import/Plex follow-up from Issue #16.

**Next:** no further Whiplash acceptance action; future control-plane work requires separately scoped authorization.

## Historical live write checkpoint — 2026-09-27 (Issue #16)

**Verified / live-write-accepted:** the owner reviewed Phase A and explicitly approved Phase B for Whiplash (2014). Live title/year resolution returned one movie, TMDb `244786`. Phase A at 15:27:44 UTC returned `requestable`, both read sources healthy with zero matching candidates, Seerr status 1, and `mutation_attempted=false`.

The exact merged `7902349` modules ran in a transient read-only Node v24.21.0 container with normalized SQLite and the DB directory mounted read-only. The older persistent checkout was not modified. After fresh wrapper preflight, one POST to `/api/v1/request` with `{"mediaType":"movie","mediaId":244786,"is4k":false}` returned `requested`; independent request read-back confirmed request **2**, TMDb **244786**, status **2 (approved)**.

Sanitized audit: contract `0.1`, timestamp `2026-09-27T15:29:53.886Z`, action `request`, reason `request_identity_readback_confirmed`, preflight `requestable`, `mutation_attempted=true`, request ID `2`, request status `2`.

Read-only Radarr verification found the same title/year/TMDb, monitored=true, has_file=false, and confirmed that profile and root match Seerr's existing defaults. The queue reports downloading with tracked status OK. **Pending:** completed acquisition, import and Plex visibility; no playback claim is made.

No direct ARR mutation, policy/configuration changes, database writes, persistent service or host upgrade were performed. The sole authorized mutation was the wrapper-mediated Seerr request; downstream acquisition follows existing policy. No executable code changed or full test suite rerun (60 tests remain the Issue #14 evidence). Cross-process serialization/uncertain-receipt limitations still apply.

**Next exact action:** read-only verification of Whiplash completion/import and Plex visibility; do not submit another request.

## Historical implementation checkpoint — Controlled Seerr wrapper — 2026-09-26 (Issue #14)

**Component-tested, live write not yet accepted:** [movie-request wrapper](../tools/movie-intelligence/SEERR.md) provides dry-run plans, stable-ID preflight, existing-availability/request/managed no-ops, a single allowlisted movie POST behind explicit execution, identity read-back and sanitized audit events. All 37 existing tests plus 23 wrapper tests pass locally. No real Seerr request was sent.

**Read-only discovery verified:** Seerr 3.4.1 search/movie/request-list and service-default GET semantics were inspected. The existing default standard Radarr service has a valid profile/root; Scarface reports available. No media policy, service/container or database changes occurred. Cross-process execution must be serialized and uncertain audit outcomes retained by the future orchestrator; the wrapper does not claim distributed exactly-once delivery.

**Next:** a separate live acceptance issue must explicitly choose/approve one movie, review its dry-run and verify one request through Seerr → Radarr → acquisition/Plex as appropriate. Live write acceptance was not started here.

## Live read acceptance — 2026-09-26 (Issue #13)

**Verified / live-accepted:** the clean existing tool checkout matched main/PR #12 commit `0382d1c6fc52336cd621d823a577cb67c9a6d88b`. The reviewed three-table schema was confirmed and `MOVIE_DB_SCHEMA_MODE=normalized` was explicitly selected. Both CLI reads exited 0 using official `node:24-bookworm-slim`, runtime v24.21.0, in transient `docker run --rm` containers.

| Live query | Movie Intelligence | Radarr | Result |
|---|---|---|---|
| Heretik (2024) | 1 candidate; ČSFD 1419147, TMDb/IMDb null | 0 candidates | `single_source_only`, `title_candidate_in_one_source` |
| TMDb 111 | 0 candidates for this ID | Scarface (1983), IMDb tt0086250; monitored=true, has_file=true; profile 9 UHD Bluray + WEB; released | `single_source_only`, `stable_id_in_one_source` |

**Identity boundary:** no live confirmed cross-source match was claimed. Zero Movie Intelligence candidates for TMDb 111 does not prove that a differently titled/identified record is absent. No IDs were invented or written; broader identity enrichment remains separate.

**ČSFD timer verified:** active/waiting, last trigger 2026-09-15 04:15:02 UTC. The matching service ran until 04:15:31 UTC, Result=success and ExecMainStatus=0; journal lifecycle records confirm successful completion. Next trigger observed: 2026-10-01 04:15:00 UTC. This verifies scheduled execution success, not a fresh audit of every imported rating.

**Safety evidence:** code and the DB directory were bind-mounted read-only; container root filesystem was read-only, extensions remained disabled, and the repository's GET-only Radarr adapter was used. DB, WAL and SHM hashes were identical before/after the CLI runs. Persistent container IDs were unchanged; host Node remained v22.22.1. No package upgrade, persistent service, schema migration, or Radarr/Seerr write/search/request action was performed. Credentials passed only through process memory/stdin; no credential files or raw personal output were committed.

**Next exact action:** separately design/implement a narrow controlled Seerr request wrapper with identity, policy and audit boundaries. Do not start that implementation as part of Issue #13.

## Media checkpoint — 2026-09-25

This checkpoint supersedes older media migration-gate and Bazarr status below. It does not re-verify unrelated infrastructure. Migration remains formally closed; a fresh torrent hardlink check is non-blocking.

### Seerr acceptance — 2026-09-25

- **Verified:** Seerr 3.4.1 is healthy in its own Compose project, attached to the existing `docker_default` media network. Port 5055 binds only the host LAN address; no public proxy or router rule was added. Plex owner authentication initialized successfully.
- **Verified:** Plex Movies and TV Shows libraries are enabled and scanned. Seerr distinguishes The Sinner season 2 as available and season 1 as missing. Radarr and Sonarr connection tests returned the intended existing profiles and roots.
- **Verified:** Request #1 for The Sinner (2017), season 1 only (TMDb 39852 / TVDB 326866), was approved in Seerr and reached existing Sonarr series #25. Profile 7 `WEB-2160p (Alternative)`, root `/tv`, path `/tv/The Sinner`. Season 1 became monitored; season 2 stayed monitored and seasons 3–4 stayed unmonitored.
- **Verified:** The pilot initially disabled immediate search. The user then explicitly requested searching; Sonarr SeasonSearch #92180 completed with 8 reports sent to SABnzbd through NZBgeek. This verifies request handoff and acquisition start, not completed download/import or playback.
- **Verified:** Radarr profile 9 `UHD Bluray + WEB` and root `/movies` configured; no unnecessary movie request created. Before/after API comparisons show identical ARR quality profiles, indexers, download clients and remote path mappings. Usenet automatic/RSS and torrent interactive-only policy remain unchanged.
- **Configuration:** Future approved requests use automatic search through ARR; no separate duplicate 4K services or conflicting quality rules. New Plex account auto-enrollment is disabled; the existing owner can sign in. Default request permissions remain approval-based.
- **Verified follow-up:** The user confirmed the real Seerr flow completed successfully on the client through Plex, closing the previously open owner-login and The Sinner download/import follow-up.
- **Still outside this acceptance:** Router reachability from outside the LAN was not independently probed; no public exposure was added or required. Movie request creation remains component-tested rather than exercised with a second real download.
- **Stop point:** Seerr is accepted end-to-end for the intended human request flow. Bazarr English-default/match-reliability and fresh torrent hardlink checks remain non-blocking follow-up. The next media work is the watched/history architecture and persistent Plex-visible My Cinema shelf.

Deployment, backups and rollback: [Seerr](seerr.md).

### Bazarr pilot evidence (prior verified checkpoint; unchanged during Seerr setup)

- **Verified:** Bazarr 1.6.1 is running and healthy. OpenSubtitles.com is configured and reports Good; login/search/download succeeded after the user confirmed the account email. No credentials are stored in Git.
- **Verified:** Only one movie at a time received the EN/CZ/SK pilot profile. Embedded English counted as present. Dune (2021) returned CZ/SK candidates at 73% without source/release-group matches; none was downloaded.
- **Verified:** Exactly one Czech subtitle was downloaded for The Game (1997): `The Game (1997) Bluray-1080p.cs.srt`, provider subtitle ID `374530`. Bazarr history records 90.56% (163/180; search UI rounded to 90%). Local video: `The.Game.1997.1080p.BluRay.DTS.x264.1-CtrlHD-Obfuscated`, 24000/1001 FPS, 7728.320 seconds. Subtitle release: `The Game 1997 1080p BluRay VC-1 DTS-HD MA 5.1-BX`.
- **Match limitation:** Title/year, nominal edition, source and resolution matched; release group, hash and audio/video codecs did not. The initial subtitle did not synchronize correctly, confirmed by the user. A high score did not prove an exact cut/runtime match.
- **Verified correction:** Against the embedded English SRT, three separated sections yielded offsets +14.68, +14.72 and +14.72 seconds, all at scale 1.000. Applied a uniform +14.72-second shift only to the newly downloaded Czech file, retaining all 1,196 cues and identical subtitle text. Original subtitle retained privately under `/data/docker/bazarr/config/pilot-validation/`. User confirmed the corrected external Czech track now synchronizes. The user also reported that another subtitle found directly through Plex had synchronized without correction.
- **Verified Plex visibility:** Refreshed only The Game. Plex lists the external Czech SRT alongside the unchanged embedded English SRT. The user confirmed external-track playback.
- **Verified account configuration:** Automatic track selection is enabled, preferred subtitle language is English, and subtitle mode is Always enabled. **Still pending:** explicit client confirmation of automatic English selection on an item without a remembered manual override. The Game currently retains the manually selected Czech track; that does not establish a default-selection defect.
- **Policy boundary:** Czech was selected ahead of the lower-scoring Slovak candidates. No equally good CZ/SK tie-break was tested, and no Slovak subtitle was downloaded. Unattended exact-match/timing reliability is not accepted based on this manually corrected sample.
- **Safe final state:** 34 movie files and 44 series synchronized; zero assigned language profiles; library default assignment and subtitle upgrades remain disabled. Exactly one movie subtitle-download record and zero episode-download records. Minimum scores remain 90% for movies and TV. Bazarr was not redeployed.

### ARR / next gate

Sonarr still reports root `/tv` and mapping `/download/` → `/data/torrents/`; Radarr reports root `/movies` and mapping `/downloads/` → `/data/torrents/`. These were inspected without changes. No old video/torrent was moved, reimported or modified to force a hardlink test. Previously inspected Tokyo Vice S02E10 source/library files had different inodes and links=1; a fresh post-fix workflow remains unverified.

**Acceptance:** Remaining Bazarr English-default and unattended-match checks are explicitly non-blocking by user decision. Broad automation stays paused. Seerr acceptance is recorded above.


## Historical migration snapshot (superseded by checkpoint above)

**Snapshot date:** 2026-09-08  
**Project phase:** migration acceptance / closure preparation

This document is the concise source of truth for what exists, what has been verified, and what remains planned.

## Desktop platform

| Item | Status | Current knowledge |
|---|---|---|
| Host | Verified | Dedicated Ubuntu Server desktop |
| Data disk | Verified | Toshiba MG09 18 TB, ext4, mounted at `/data` |
| Media stack | Verified running | Plex, Radarr, Sonarr, Prowlarr, qBittorrent, SABnzbd |
| Kometa | Verified | Pinned 2.4.8; five Movies discovery collections accepted; daily bounded runner; optional Home publishing requires Plex Pass |
| Recyclarr | Verified | Separate Compose project managing Radarr and Sonarr TRaSH configuration |
| Tautulli | Verified | Re-authorized and healthy |
| Netdata | Verified | Local diagnostics dashboard deployed |
| nzb360 | Configured / deferred | Local service connections configured; paid unlock/subscription deferred |

## Migration acceptance

Verified:

- desktop cutover and data migration;
- recovery archive checksum match;
- all persistent services running;
- real Radarr post-migration download/import;
- Sonarr end-to-end Usenet acquisition/import;
- real Usenet failure blocklisting and automatic fallback behavior;
- Plex reclaim after credential invalidation;
- TV clients working again after Plex reclaim;
- Kometa Plex/TMDb connectivity;
- historical Kometa `Newly Released` Home/Recommended observation; current publishing is gated by Plex Pass;
- Tautulli re-authorization;
- SAB API/NZB credential rotation after accidental disclosure;
- historical ext4 hardlink recovery for migrated torrent/media duplicates.

One final live acceptance check remains:

- a real torrent has been grabbed and is still downloading;
- after completion, verify import and real hardlink behavior while qBittorrent retains the file for seeding.

## Current gate

Complete the in-progress torrent acceptance check. If import and hardlink behavior pass, the media workstream is ready to hand back to infrastructure for formal migration closure.

## Storage / performance finding

Netdata confirmed that heavy SAB download/repair/unpack activity can saturate the 18 TB media disk and drive very high CPU iowait, degrading both Plex playback and SAB throughput.

This is a real issue, but it is no longer treated as a migration blocker. The preferred direction is a future dedicated SAB scratch area, likely on SSD. Exact device, size and filesystem remain undecided.

## Recovery state

The old external source/rollback disk is confirmed still attached read-only.

Its final disposition is intentionally deferred to formal migration closure, where it can be deliberately unmounted, archived, repurposed or wiped.

## Acquisition policy

- Usenet is automatic/default.
- Torrents remain manual/Interactive Search fallback.
- CZ/SK torrent sources remain manual.
- Failed Usenet releases are blocklisted and another candidate can be attempted automatically.

## TRaSH / Recyclarr

Radarr:
```text
UHD Bluray + WEB
```

Sonarr:
```text
WEB-2160p (Alternative)
```

The shared philosophy remains sensible 2160p, 1080p fallback, no mass upgrades, and manual exceptions for very large remuxes or special cases.

## Kometa direction

Kometa v2 proceeds iteratively as ordinary collections. Phase 2A's five Movies
collections are accepted; assess their usefulness before adding further concepts.
Home/Recommended publishing is optional and requires active Plex Pass.

## Remaining open issues

- Finish the current torrent download and verify import + hardlink behavior.
- Hand media acceptance back to infrastructure.
- Decide what to do with the old rollback disk.
- Replace/remove the temporary migration-version override during infrastructure closure.
- Harden backup/restore procedures and retention.
- Later: dedicated SAB scratch storage, likely SSD, followed by repeat Netdata/Tautulli load testing.
- Revisit nzb360 paid unlocks only if worth it in practice.
- Later media backlog: Kometa iteration, Bazarr, Trakt/PlexTraktSync, Seerr, controlled remote access, media agents.

## Evidence boundary

The remaining torrent hardlink check is **not yet verified** because the live download is still incomplete. Future SAB scratch-storage design is also undecided and should not be represented as implemented.
