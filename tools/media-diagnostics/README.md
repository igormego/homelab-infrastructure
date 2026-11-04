# Read-only Media Diagnostics v0.1

**Status: source-only, not deployed, not a live inspection of the user's Homelab.**
Implements a reusable bounded Plex/Sonarr/Bazarr evidence collector for
[Media diagnostics Issue #47](https://github.com/igormego/homelab-infrastructure/issues/47).
The shared backend/resource access design belongs to
[Personal AI Brain #83](https://github.com/igormego/personal-ai-brain/issues/83).

## What it checks

- **Plex versions:** Enumerates matching TV episodes and reads exact metadata via
  `checkFiles=1`; reports Media/Part entries, distinct available file *paths*,
  unavailable entries, codecs, resolutions and reported average bitrate. It
  does **not** claim a unique inode or physical file without an authorized host
  filesystem stat. Two different Plex paths may still be hardlinks.
- **Sonarr:** Looks up candidate series titles, recent imported episodes and
  episode IDs; never treats an arbitrary Dexter series or episode as selected
  when several recent candidates exist. If unknown, leaves candidates visible.
- **Bazarr:** Reads indexed episode subtitle status and missing languages using
  the documented `GET /api/episodes?episodeid[]=` endpoint. It **does not
  execute `GET /api/providers/episodes`**, which triggers a live provider
  search, indexing and quota-consuming external calls despite using GET.
  The collector does not infer why a subtitle was rejected without actual
  provider matching/score, language profile and settings evidence. The
  settings API may contain secrets, so it is not fetched in v0.1.
- **Plex bitrate comparison:** Reads `Media.bitrate` in **kbps** for the exact
  episode and candidate movie(s). These are media metadata values, not a
  Tautulli/Plex measured playback session or proof that the TV display's
  Mbps label was an average file bitrate. Missing playback evidence stays
  `not_observed`. No automatic quality selection or transcoding.
- **Ambiguous titles:** Returns up to five recent episode candidates and eight
  matching movies, with their year/ratingKey. No silent choice of a `Carrie`
  year, release or episode in a different `Dexter` series.

## Explicit trusted operator invocation (private network only)

The host operator supplies already approved endpoint/credential pairs through
the process environment; values must **never** be committed, written to
issues, printed, or passed as command arguments. Only authenticated
fixed-path GET endpoints are used; redirect responses are refused and
results are size/time bounded. Example of the *shape* of a private invocation:

```sh
# Requires trusted private environment variables:
# SONARR_BASE_URL, SONARR_API_KEY,
# PLEX_BASE_URL, PLEX_TOKEN,
# BAZARR_BASE_URL, BAZARR_API_KEY,
# PLEX_TV_LIBRARY_ID, PLEX_MOVIES_LIBRARY_ID.
python3 tools/media-diagnostics/probe.py --series Dexter --movie Carrie
```

The result is **private media history**. Never copy its raw JSON into a public
repository/CI artifact. It contains file basenames, release information, movie
titles and episode IDs, but file locations are hashed rather than included as
absolute paths. `--episode-id` and `--movie-year` are optional explicit
selectors to disambiguate candidates; the normal natural-language agent route
should fill these selectors from trusted context when possible.

This source-only CLI is not an arbitrary remote execution channel and
has **no** download, subtitle search, subtitle write, search command, rescan,
Plex deletion, watched-state mutation or service restart operation. It cannot
verify Docker bind mounts, physical hardlinks or Bazarr match scores solely
through current exposed APIs. Those remain separate trusted host-level
read-only evidence gates.

## Regression / acceptance

```sh
cd tools/media-diagnostics
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s . -p 'test_*.py'
```

Required GitHub CI runs synthetic fixtures for phantom Plex versions,
missing SK/CZ subtitle status, 25/14 Mbps media bitrate, source failures
and endpoint restrictions. Source tests **do not** prove live connectivity.

**Next:** operator validates actual service API/version schemas, then builds
the minimal host-only sanitized snapshot provider and connects this *same*
generic diagnostic resource to the existing Personal AI Brain Resource
Broker. Future ordinary ChatGPT requests should receive the verified,
time-stamped result without manual screenshot, CLI prompt or Codex task.
No service write/grant follows from read-only acceptance.
