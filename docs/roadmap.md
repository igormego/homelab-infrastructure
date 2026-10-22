# Roadmap

## Historical archive renderer and Heretic pilot — 2026-10-22

Renderer fix [PR #30](https://github.com/igormego/homelab-infrastructure/pull/30)
is merged; 26 offline tests and actual 60-second H.264 rendering/ffprobe passed.
The original pre-publication failure receipt was preserved with recovery evidence.
One approved retry created exactly one Heretic placeholder in the isolated archive;
identity, poster, metadata and file accessibility are verified. Existing Movies,
Radarr, Zodiac, Seerr selection and media.db remained unchanged.

**Server acceptance PASS:** [PR #31](https://github.com/igormego/homelab-infrastructure/pull/31)
replaces the blanket Unwatched guard with canonical identity/account-scoped
history preservation; 38 offline tests passed. Heretic's inherited watched count
and timestamp match the historical Movies event. The existing item was verified
read-only and only its private receipt was reconciled, retaining the original
receipt and all failure/recovery evidence. Repeated verification is idempotent.
No scan, playback, media regeneration or watched-state mutation occurred.

Next: Heretic Samsung poster/details confirmation. No bulk archive, retry or
automatic lifecycle is authorized. See the authoritative
[Media state](../.project/workstreams/media-automation.yaml).

## Plex identity corrected; reacquisition dry plan PASS — 2026-10-21

Owner-authorized Match of the single unmatched Peaky Blinders movie restored
TMDb/IMDb identity. Private snapshots verified preserved file availability,
watched state/history, collections and other library item identities.
Heretic and One Battle After Another now pass the unchanged preflight. Heretic's
`execute=false` plan is ready; Radarr remains unmonitored/no-file and the durable
operation journal is empty. Next: separate approval of one reviewed live execution.
Malignant, archive placeholders and all acquisition settings remain untouched.
The earlier blocked preparation checkpoint below is historical.


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


## My Cinema safe reacquisition — 2026-10-20

- **Component-tested:** separate [reacquire_movie v0.1](../tools/movie-intelligence/REACQUIRE.md),
  monitor-only Radarr update plus one search, reviewed plan, durable replay guards;
  123 tests pass. Ordinary `request_movie` and media policy remain unchanged.
- **Next:** separately authorize deployment/private journal provisioning and one
  qualifying managed/unmonitored/no-file candidate's live acceptance. No real
  request or reacquisition has been executed. Bulk archive lifecycle work remains deferred.

## Kometa v2 Phase 2A — runtime accepted — 2026-10-16

- **Verified:** October 15/16 scheduled runs; five Movies collections with October 16 counts 2/3/1/4/20, deterministic
  persisted rotation, ordered namespace, and preserved existing browsing directors.
- **Component-tested:** Trending-only deletion below three local matches and recovery;
  fix deployed, 16 tests passed. Global retention remains unchanged.
- **Next:** optionally observe a natural below-minimum Trending day/recovery and gather
  usefulness feedback. Daily/Thursday rotations passed; only Scorsese qualifies for the director pool.
- Ordinary collections do not require Plex Pass. Home/Recommended publishing remains
  optional and outside acceptance. See [Kometa policy](../tools/kometa/README.md).
- Mood, Critically Acclaimed, Hidden Gems, Rediscover, Because You Watched,
  My Cinema and all TV v2 work remain deferred.

## Kometa v2 Phase 1 — 2026-10-14

Historical checkpoint; the Phase 2A section above owns the current next action.

- **Verified:** pinned Kometa 2.4.8 one-shot runner, daily 03:15 UTC cron entry,
  non-overlap lock, timeout, private logs and successful single-row live execution.
- **Component-tested:** Newly Released promotion persisted for owner Home and Movies
  Recommended; row rendering requires the currently inactive Plex Pass entitlement.
- **Next:** owner decision on the Plex Pass prerequisite; observe the first
  scheduled invocation. See the [acceptance record](../tools/kometa/README.md).
- Directors, TV configuration, overlays and the broader v2 layout remain deferred.

## Media parked — 2026-10-07

Media work is parked. Mortgully remains ambiguous and must not be retried automatically; inspect the saved POST audit only when the Media workstream resumes. Public-indexer torrent cleanup is accepted end-to-end. Media services, identity, watch history and policy remain owned by Homelab/Media; Personal AI Brain may reuse generic capability, cursor, idempotency, audit and delivery-state patterns without taking ownership of Media semantics.

## First ongoing Trakt delivery — ambiguous safe checkpoint — 2026-10-06

- Durable `in_flight` created for Mortgully.
- Exactly one real Trakt POST attempted.
- POST result is ambiguous; immediate and follow-up item-specific reads found **0 matching events**.
- No history ID captured; duplicates 0; delivery state `ambiguous`; DB integrity OK; Plex writes 0.
- **No automatic retry.**
- **Next:** inspect the sanitized saved POST response/audit only. If it definitively proves no event was created, decide separately whether to allow one explicit retry; otherwise keep the delivery ambiguous.

## Ongoing Trakt delivery — ready for first real write — 2026-10-06

- HTTP 403 root cause resolved: missing explicit `User-Agent`; failed refresh path also used the legacy host.
- Public Trakt read: HTTP 200. OAuth-required read: HTTP 200. Current access token remains valid; no refresh needed.
- Mortgully resolved to the correct internal Trakt episode identity.
- Item-specific freshness check: HTTP 200, **0 existing history events**.
- No delivery row and no POST exist yet.
- **Next:** `in_flight` -> exactly one real Trakt POST -> immediate exact read-back -> capture Trakt history ID -> mark success. No further observation-only or identity ceremony is needed unless the write path itself exposes a new issue.

## Ongoing Trakt delivery — blocked on OAuth/API 403 — 2026-10-06

- First target selected: Rick and Morty, *Mortgully: The Last Rickforest*.
- Read-only Trakt identity lookup returned HTTP 403; OAuth refresh also returned HTTP 403.
- Safe stop occurred **before** `in_flight` creation and before any Trakt POST.
- Ledger remains 3 events / 3 external links / 0 deliveries; DB integrity is OK.
- **Next:** narrow OAuth/API 403 diagnosis only. Once read-only access is restored, resume at identity lookup/freshness check and proceed to the already-approved single live Trakt delivery. Do not retest earlier accepted layers.

## Ongoing watched-sync observation automation — accepted — 2026-10-06

- **PASS:** merged Tautulli reconciliation runner accepted live on a bounded real window.
- Cursor safely bootstrapped `38 -> 42` and advanced only after durable handling.
- Two eligible Rick and Morty episode events were inserted with correct identity, timestamps, provenance and source IDs.
- Replay/re-entry produced zero duplicate rows and preserved cursor/state.
- Trakt writes: 0. Plex writes: 0. DB integrity check passed.
- **Next:** one controlled real `media.db -> Trakt` delivery. This is the next unverified boundary; no additional observation-only ceremony is required without new evidence.

## Public ARR torrent cleanup — accepted end-to-end — 2026-10-14

- Ten confirmed public Prowlarr torrent indexers now use a **1-minute seed-time goal** with ratio unset.
- Sk-CzTorrent is intentionally unchanged until its seeding/ratio requirements are known.
- qBittorrent global unlimited policy is unchanged; ARR Completed Download Handling/removal is unchanged.
- No existing torrents/data were deleted when applying the policy; private rollback snapshot exists.
- **PASS:** fresh Radarr/Knaben grab of *Scream 7* received the one-minute policy; qBittorrent stopped after 70 seconds, Radarr imported the movie and then removed the torrent job and download-side content.
- Older The Pitt S01E06 remained unlimited, but its timing relative to policy application was not established; it is historical/non-diagnostic rather than a failure of the current accepted path.
- Cleanup acceptance is closed. Reopen only if a future qualifying public-indexer ARR grab contradicts this behavior.

## ARR torrent cleanup exact zero-seed — deferred — 2026-10-06

- qBittorrent 5.1.4 cannot apply native category share limits.
- Prowlarr 2.3.5.5327 cannot safely synchronize a zero-valued seed goal.
- qBittorrent 5.2.4 was preflighted but its released WebUI/WebAPI still does not reliably expose the required category share-limit fields for this headless stack.
- **Decision:** no upgrade and no cleanup mutation for now.
- Revisit when a released LinuxServer qBittorrent build exposes stable category limit configuration, or only if a short positive seed-time compromise is explicitly accepted.

## Hardlink acceptance closed — 2026-10-06

- **PASS:** fresh post-fix Sonarr import for The Pitt S01E04 is a true hardlink: same device, same inode, link count 2, exact size match.
- ARR import/removal behavior is healthy; the remaining source retention is caused by qBittorrent's unlimited seeding policy.
- **Next policy decision:** choose zero-seeding cleanup scope. Global ratio `0.0` is suitable only if all qBittorrent torrents should stop immediately; otherwise prefer ARR-category-specific cleanup if supported.
- No cleanup/seeding configuration change has been applied yet.

## Tautulli reconciliation runner milestone — 2026-10-06

- **Merged:** PR #10 squash-merged; main `0285d235c55b5d1d73c9eaaa12ae487f484e7e10`.
- **Verified:** required CI green; **118 tests passed** after merge; six-file scope unchanged; secrets/security checks clean.
- No Plex/Trakt writes or deployment were introduced.
- **Next:** bounded live observation-only acceptance of incremental reconciliation and durable cursor behavior.
- Hardlink acceptance remains a parallel open loop; a fresh post-fix check is currently in progress.

## Ongoing watched-sync acceptance — 2026-10-06

- **Verified PASS:** first real episode observation accepted for Rick and Morty S09E06 (*Erickerhead*) at `2026-10-06T13:20:09Z`.
- Stable episode identity resolved via TMDb/IMDb/TVDB; exactly one canonical watch event and one external event link were stored.
- Immediate replay was idempotent; no duplicate event was created.
- No Trakt or Plex write occurred.
- **Next:** separate Change/Review PR for a restart-safe Tautulli reconciliation runner with durable source cursor; keep it observation-only until live-accepted.

## Hardlink acceptance follow-up — 2026-10-06

- **Verified FAIL (pre-fix):** a fresh completed Sonarr torrent import produced separate source/library copies; same device and size, different inodes, link count 1 on both.
- **Root cause confirmed:** source and library were addressed through separate Docker bind mounts.
- **Fix applied:** Remote Path Mapping now normalizes `/downloads/` -> `/data/torrents/`, and 50 Sonarr series paths were rewritten to `/data/media/tv` with `moveFiles=false`. No container recreation or media move occurred.
- **Read-back accepted:** mapping and all 50 logical paths persisted; 11 absent series directories were equally absent through the old alias; 475 recorded episode files were checked with 0 missing.
- **Remaining gate:** repeat acceptance on one **future fresh post-fix import** while the torrent source is retained. PASS requires same device, same inode, matching size and link count >= 2.

## Watched-history milestone — 2026-10-06

- **Verified complete:** historical Trakt backfill covers all 558 watched movies.
- Stable identity enrichment, ambiguity resolution, provenance handling, duplicate protection, read-back verification and private rollback capture are complete for the historical set.
- The final Backrooms exception used a policy-compliant release-date `legacy_placeholder`; its old Plex event remains intentionally unlinked rather than falsely inferred.
- Plex historical watched-date backfill has **not** started and remains a separate gate.
- **Next media choices:** close the fresh real torrent hardlink acceptance check if a suitable candidate exists; design ongoing Plex/media.db/Trakt synchronization for new watches; or begin Kometa v2 iteration now that watched/history behavior is stable.

## Stable identity enrichment and history preflight — 2026-10-05

Verified follow-up: all five previously excluded records now have confirmed TMDb and IMDb mappings, bringing the identity-qualified cohort to 40. Exact source metadata, credits and release history resolved festival/distribution year differences and rejected wrong-work hints. Existing external_ids was reused with a fresh SQLite-aware backup, disposable rehearsal, transactional invariant checks and reopened verification: 10 additive rows, no schema changes, no collisions, all pre-existing rows unchanged. A read-only history preflight qualified 39 proposals: two Plex-confirmed dates and 37 legacy placeholders; one unresolved Plex linkage is excluded. Zero cohort titles already exist in Trakt. All six previously accepted Trakt events remain unchanged and excluded. No Trakt/Plex/watch-date writes occurred.

The private 39-entry proposal is ready for separate review/authorization. Release-date placeholders are not genuine viewing dates; exact times are synthetic local noon. CSFD rating timestamps are never treated as viewing dates. Private identity/date/event evidence is not published.

## Infrastructure control plane — 2026-09-27

- **Verified / live-accepted (Issue #13):** normalized Movie Intelligence + Radarr reads on PR #12 commit `0382d1c`, transient Node v24.21.0; system Node v22.22.1 unchanged. Heretik is Movie-Intelligence-only; TMDb 111 returns Radarr-only Scarface with the expected file/profile state. No confirmed cross-source identity was fabricated.
- **Verified:** unattended ČSFD run on 2026-09-15 completed successfully (exit 0, journal confirmed); timer remains active, next observed trigger 2026-10-01 04:15 UTC.
- **Component-tested (Issue #14):** [controlled Seerr movie-request wrapper](../tools/movie-intelligence/SEERR.md), dry-run default, stable identity, one allowlisted POST, duplicate/managed no-ops and uncertain-outcome handling; 60 tests pass. No live request was sent.
- **Verified / live-write-accepted (Issue #16):** owner-approved Whiplash (2014), TMDb 244786; one wrapper POST, request 2 approved and identity read-back confirmed. Radarr handoff/default profile/root verified; downloading, no file yet.
- **Verified (Issue #17, 2026-09-27):** Whiplash import completed; Radarr has_file=true, managed file exists with matching size. Plex confirms TMDb 244786 and the exact imported file. Seerr request 2 completed. Playback not tested.
- **Next:** no remaining Whiplash acceptance work; choose the next separately scoped control-plane task. No duplicate request or direct ARR mutation.
- Detailed source counts, limitations and safety checks are recorded in [current state](current-state.md#live-read-acceptance--2026-09-26-issue-13).

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
- **Stop point:** Seerr is accepted end-to-end for the intended human request flow. Bazarr English-default/match-reliability and fresh torrent hardlink checks remain non-blocking follow-up. Next media focus: persistent watched/history architecture and My Cinema.

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

**Acceptance / sequencing update (2026-09-25):** Subtitle acquisition, Plex visibility and corrected Czech timing are verified on one film. Broader Bazarr automation remains intentionally paused because unattended exact-match reliability and the clean English-default client check are still open. The user explicitly chose to keep those as non-blocking follow-up and proceed to Seerr now. Do not enable library-wide Bazarr profiles, bulk subtitle downloads or upgrades while this remains open.


## Historical migration roadmap (superseded by checkpoint above)

## Now

- Finish one fresh real ARR torrent import after the shared `/data` hardlink fix.
- Verify successful import and matching inode/link count while qBittorrent keeps seeding.
- If that passes, issue the media-workstream completion handoff to infrastructure.
- Keep the old rollback disk read-only until infrastructure performs formal migration closure.

## Current checkpoint

The desktop media stack is effectively at final acceptance:

- Plex works on the reclaimed desktop server and TV clients.
- Radarr and Sonarr use TRaSH/Recyclarr-managed profiles.
- Usenet-first automation and failed-release fallback are verified.
- Kometa 2.4.8 is revalidated.
- Five Movies discovery collections are accepted; Home/Recommended publishing remains optional and gated by Plex Pass.
- Tautulli is healthy after re-authorization.
- Netdata monitoring is deployed.
- SAB API/NZB credentials were rotated.
- nzb360 local integrations are configured; paid unlocks are deferred.
- The old rollback disk remains attached read-only.
- The ARR hardlink topology has been corrected with a shared `/data` mount and qBittorrent Remote Path Mapping; an in-container hardlink test passed, but one fresh real ARR import still remains for end-to-end acceptance.

## Current gate

Finish the real torrent import/hardlink check, then close the media acceptance phase.

## Next: infrastructure closure

- Decide the final disposition of the old rollback disk.
- Replace/remove the temporary migration-version override.
- Finalize permanent container image pin/update policy.
- Remove the obsolete Compose `version` field if still present.
- Harden backup/restore and retention.
- Refresh public-safe documentation/config examples as needed.

## Near-term media work after closure

### 1. Bazarr / subtitle automation

This is the next application priority after migration closure.

Subtitle policy:

- English subtitles remain the primary/default preference.
- In many releases English subtitles will already be embedded or present; Bazarr should avoid creating unnecessary duplicates.
- Bazarr should additionally search for high-quality Czech and Slovak subtitles when available.
- Czech should be preferred over Slovak when both are viable, mainly because a good release/version match is expected to be available more often.
- CZ/SK subtitles should match the exact release/version as closely as possible so timing and cuts line up without manual repair.
- CZ/SK subtitles are optional alternate tracks, not replacements for English; the user can switch to Czech or Slovak manually in Plex when wanted.
- Reduce the current need to manually find and pair CZ/SK subtitles, while retaining manual override for difficult releases.
- Define sensible movie/TV language and scoring rules, with provider priorities tuned for CZ/SK subtitle quality and release matching.

### 2. Seerr

- **Verified:** deployed and accepted on 2026-09-25; see the current checkpoint above;
- use it as the human-friendly discovery/request layer over Radarr/Sonarr;
- preserve existing Radarr/Sonarr quality profiles and Usenet-first/manual-torrent acquisition policy rather than duplicating them in Seerr;
- keep the deployment/policy media-owned while the sibling Agent Control Plane may later consume the Seerr API as a controlled write gateway.

### 3. Persistent watched-library / personal cinema shelf — next active design

Design a Plex-visible permanent collection of titles already watched, even when the media file is no longer stored locally.

The goal is not only watch history: it should work as a visual personal film/TV bookshelf where browsing old posters can trigger memories and rediscovery.

Design principles:

- watched history survives deletion of local media;
- local availability and watched-history are separate states;
- the watched shelf should remain visually browsable inside Plex;
- test the cleanest native Plex/List approach first;
- if the TV-client experience is insufficient, evaluate a dedicated archive/placeholder-library approach;
- later connect this layer to the planned media database / ČSFD enrichment and recommendation agents.

### 4. Trakt / PlexTraktSync

- synchronize watched state and ratings outside Plex;
- provide a durable history source independent of individual media files;
- evaluate whether Trakt should become one of the canonical inputs for the future Media Brain.

### 5. Kometa v2 iteration

- optionally observe natural Trending below-minimum deletion/recovery and assess Phase 2A usefulness;
- scope additional Movies concepts separately; do not add TV or more rows implicitly;
- evaluate optional Home/Recommended publishing only if Plex Pass becomes active.

### 6. Mobile / remote access

- revisit nzb360 premium only if the mobile cockpit proves useful;
- later add controlled remote administration via VPN/Tailscale/WireGuard rather than exposing admin ports.

### 7. Media agents

Only after the base media platform is stable:

- Observer: watches library/acquisition/quality/subtitle state and surfaces issues;
- Curator: maintains collections, watched shelf, metadata/enrichment and recommendations;
- Media Operator: performs approved downloads, upgrades, cleanups and routine actions;
- destructive actions remain confirmation-gated until the policy is mature.

## Post-closure performance optimization

A real Netdata investigation confirmed SAB/Plex contention on the 18 TB media disk.

Preferred direction:

- move SAB incomplete/complete repair/unpack scratch workload to separate storage;
- SSD is currently the likely target;
- exact device, capacity and filesystem are still open;
- repeat Plex + SAB load testing after the change.

This optimization should not hold migration closure hostage.

## Open decisions

- final disposition of the rollback disk;
- permanent container update/pin policy;
- future SAB scratch storage device/layout;
- backup tooling, retention and restore-test cadence;
- exact Plex implementation for the persistent watched-library shelf;
- role split between Plex watched state, Trakt and the future media/ČSFD database;
- whether/when nzb360 is worth paying for.
