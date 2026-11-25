# osu-coach data contract (MVP)

The fetcher writes; the coach only reads `data/` and writes `coach/`.

`session end --json` includes `map_errors` for beatmaps that return HTTP 404 or invalid metadata/attributes; the session closes with the scores retained and those map attributes unavailable. `fetch beatmaps --json` reports the same failures in `errors`. A completed session map pass is saved as `map_sync.json` and exposed in current and previous `context` entries; it contains the completion time, score-sync timestamp at that pass, map and fetch counts, and errors. A later successful pass replaces it. A null record means no completed pass was recorded, not necessarily that attributes are absent. Compare its score-sync reference with the current score sync and inspect the results; even matching timestamps do not prove complete score coverage. Network errors and other HTTP errors still fail the command for retry and do not replace the last completed map pass. A missing attribute is unknown, not a zero difficulty or proof the map is unplayable.

During an open session, `session observe TEXT` appends a player observation to `session.json` as `{ "reported_at": "timestamp", "text": "..." }`. `context` exposes these observations as part of the session, including previous sessions. The timestamp is when it was recorded and does not establish when the reported experience occurred, which block caused it, or a diagnosis. The text is a report, not a validated measurement. Older sessions may omit `observations`; after closing a session, use `session update --notes` for retrospective notes.

`session observe TEXT --beatmap ID` optionally links a report to a map explicitly identified by the player. `history ID` returns matching reports in `map_observations`, even if that session has no score file. An absent report does not mean no difficulty, and the link alone does not establish the relevant play, mods, timing or cause.

`osucoach context` returns the resolved absolute `workspace` path. Coaching files and generated plans/reviews are relative to it, which may differ from the process working directory. The path does not expose credential contents.

```
data/
  account.json                     # binding for subsequent player fetches; legacy data may be unverified
  profile/YYYY-MM-DD.json          # snapshot per fetch
  best.json                        # top 100, refreshed each fetch
  best_sync.json                   # timestamp and count of last successful best-score fetch
  map_tags/<beatmap_id>.json       # user-supplied, mod-independent labels
  beatmaps/<beatmap_id>_<mods>.json   # cache key for the full mod combination and settings (NC=DT, DC=HT)
  beatmaps/_meta/<beatmap_id>.json    # raw nomod CS/AR/OD/BPM/length/counts captured from score objects (cache)
  current_session                  # id of the open session, absent when none
  sessions/<session_id>/
    session.json                   # created/updated only via the CLI (see session.json below)
    scores.json                    # scores set during the session, incl. fails
    scores_sync.json               # most recent successful recent-score request metadata
    map_sync.json                  # most recent completed session map pass, including per-map errors
    replays/*.osr                  # optional in MVP
    replay_stats.json              # optional in MVP
coach/
  player.md                        # stable context, setup, goals
  journal.md                       # one entry per session, append-only
  plans/<session_id>.md            # pre-session plan
  reviews/<session_id>.md          # post-session review
```

`session_id` = `YYYY-MM-DD_HHMM` (local time, session start).

## Sources (osu! API v2, ruleset `osu`)
- `GET /users/{id}/osu` -> profile
- `GET /users/{id}/scores/best?limit=100` -> best.json
- `GET /users/{id}/scores/recent?include_fails=1&limit=100` -> scores.json
  - recent only covers ~24h, so fetch right after the session
- `POST /beatmaps/{id}/attributes` with mods, or rosu-pp locally -> beatmaps/

## Account provenance

`account.json` binds future profile and score fetches in a workspace to a positive numeric `OSU_USER_ID`. A different configured user is rejected; use a separate workspace for another player. Older workspaces bound to a username cannot be automatically matched to a numeric ID; use a separate workspace and independently verify any legacy records. `legacy_data_present` is true when profile, best, or session score files existed before binding. The binding does not identify those files. A missing binding also leaves provenance unknown. New score rows retain API `user_id` when supplied; null or absent remains unknown. Known numeric user mismatches are rejected before merging session scores. A binding is a guard against future configuration changes, not proof of the origin of legacy data.

`gap` rejects conflicting known numeric player IDs across the profile, best scores and workspace binding. Its `identity_evidence` reports the profile ID, the number of best scores with numeric IDs, total best scores and the workspace binding. Missing IDs, a nonnumeric binding or legacy data do not prove shared provenance, and matching IDs do not establish snapshot freshness.

## profile/YYYY-MM-DD.json
```json
{"fetched_at": "ISO8601", "pp": 8439.0, "global_rank": 18415, "country_rank": 352,
 "accuracy": 98.84, "play_count": 34434, "play_time_s": 0,
 "rank_threshold_10k_pp": 9605.0}
```
`rank_threshold_10k_pp`: pp of #10,000 (rankings page 200, last row). Optional but useful.
`rank_thresholds` optionally maps rank strings to objects containing `pp` and `fetched_at`. `fetch threshold --rank 100` records the #100 threshold. The legacy #10,000 field is still populated for compatibility. Thresholds change over time, and each timestamp identifies its own fetch.

`best_sync.json` contains `fetched_at` and `scores_returned` for the most recent successful best-score fetch. Older installations may lack it. Editing cached map details can change the modification time of `best.json` without fetching new scores, so the file modification time is not a reliable fetch timestamp.

## best.json / scores.json (array, one object per score)
```json
{"score_id": 0, "beatmap_id": 0, "beatmapset_id": 0, "title": "", "version": "",
 "created_at": "ISO8601", "mods": ["HD","DT"], "passed": true,
 "accuracy": 0.985, "max_combo": 0, "map_max_combo": 0,
 "n300": 0, "n100": 0, "n50": 0, "miss": 0,
 "pp": 0.0, "weight_pct": 100.0, "rank": "S",
 "retry_index": 1}
```
- `retry_index`: Nth recorded attempt of that beatmap within this session, regardless of mods, computed from score end-time order. Map stats retain `max_retry` as the largest such index for compatibility; it is not a retry count.
- `timestamp_source` identifies whether `created_at` came from `ended_at` or a start-time fallback. If only a start time is available, session filtering uses that fallback and may miss plays that began before the session. Such records remain in overall statistics but are excluded from block assignment and counted in `plays_with_unknown_end_time`; ordering-based summaries can also be uncertain. Older rows may omit `timestamp_source`, so their timestamp provenance cannot be recovered and they retain legacy end-time treatment in block summaries.
- Nonempty session stats include `ordering_evidence`: counts of start-time fallbacks, unknown timestamp sources, tied timestamp groups and scores in those groups. Times are compared as instants across time zones. Ties are sorted by score ID for deterministic output, not proven chronological order. Missing plays and overlapping play intervals can also limit ordering; zero flagged rows does not prove complete or comparable data.
- Map stats also provide `consecutive_recorded_repeats`: the number of recorded scores immediately preceded by a score on the same beatmap with the same mods and settings. It ignores mod order, does not prove the player used the retry button, and cannot account for missing or unsubmitted attempts or intervening breaks.
- `legacy_score_id` and `build_id` preserve the API values when available. A non-null legacy score ID identifies a stable-origin score; a null or missing value alone does not establish client or gameplay rules. Older saved scores may omit both. Classic mod settings and client rules can affect comparability.
- Newly fetched scores preserve explicit boolean `processed`, `ranked`, `is_perfect_combo` and `legacy_perfect` values, otherwise null. `processed` concerns server-side processing including pp; a null pp is not automatically an unranked result. `ranked` concerns leaderboard visibility and does not alone establish pp eligibility. The two perfect-combo fields use different client logic. Older saved records may omit these values.
- `pp` may be null for fails/unranked; keep the row anyway. `weight_pct` is only set for best.json, null for session scores.
- `created_at` is the score's `ended_at` (UTC). `map_max_combo` is null until attributes for the matching mod combination exist. Older saved scores may retain a value from a differently modded cache until scores are fetched again.
- `started_at` is the API's score start time when available, otherwise null. Older saved scores may omit it.
- `mod_settings` (optional): present only when a mod has settings, e.g. `{"DT": {"speed_change": 1.3}}`.
- Lazer score format: `n300/n100/n50/miss` = `statistics.great/ok/meh/miss`.
- New scores retain the source `raw_statistics` and `raw_maximum_statistics` dictionaries when supplied, including additional hit-result types. A null value means no usable dictionary was supplied; an empty dictionary is distinct. The maximum describes the map's possible results and is not automatically a count of objects reached during a failed play. Values are not validated or interpreted as replay diagnoses; client rules, mods and score origin affect meaning. Older records omit these fields and may need refetching.
- New score records retain API `total_score`, `classic_total_score` and `legacy_total_score` when supplied, otherwise null. They are distinct score representations, not interchangeable. A saved solo score does not establish a tournament match result, ScoreV2 value, team total or client rules. Older records omit these fields and require refetching when available.
- When the statistics dictionary is absent, those counts are null. Within a present dictionary, omitted hit results are treated as zero; explicit nulls and invalid counts are unknown. Counts must be non-negative integers to enter hit-result totals, including for older saved rows. Scores saved by older versions may contain zeros that cannot be distinguished from missing source statistics without refetching.
- Session and map stats include `hit_results` with totals for scores that have all four counts, plus the number of scores covered and the total scores. Null totals mean no complete counts were available. These aggregates include fails and retries, which can have different played lengths. Miss counts do not identify aim or tapping causes.
- `acc_passed` in session, block, window and map stats is the unweighted mean accuracy of passed scores with finite numeric accuracy in the API's 0 to 1 range. `acc_passed_samples` gives its denominator. Failed plays and passed scores with missing or invalid accuracy are excluded; raw saved score values remain available for inspection.
- Pass rates use only scores with an explicit boolean `passed` value, with `pass_rate_samples` giving the denominator. Missing or malformed pass status remains unknown and is excluded from pass/fail difficulty comparisons. Older score files may have converted missing status to false, which cannot be recovered without refetching.
- `avg_sr_passed` and `avg_sr_failed` are unweighted means over recorded scores with finite, non-negative cached star ratings. Their corresponding `_samples` fields give the number included; missing or invalid ratings are excluded. A valid-looking but stale attribute can still limit comparisons.

## beatmaps/<beatmap_id>_<mods>.json
```json
{"beatmap_id": 0, "mods": ["DT"], "star_rating": 0.0,
 "aim": 0.0, "speed": 0.0, "slider_factor": 0.0, "speed_note_count": 0.0,
 "ar": 0.0, "od": 0.0, "cs": 0.0, "bpm": 0.0, "length_s": 0,
 "n_circles": 0, "n_sliders": 0, "n_spinners": 0}
```
Values are mod-adjusted (DT changes AR/OD/BPM/length). BPM is null when the API does not provide it. Extra field `max_combo` (null if unknown).
New attribute files include `fetched_at` and `mod_settings`. The versioned cache key distinguishes all supplied mods and settings, because visual and other mods can affect difficulty. Older modded cache files used incomplete keys or mixed-format API requests and are not used by the current lookup; fetch attributes again to populate them. Nomod files remain usable. Older files may omit it. `fetch beatmap ID --mods X --refresh` requests fresh map metadata and difficulty attributes for that mod combination. Other cached mod combinations retain their previous values until refreshed. Algorithm changes can make old cached ratings stale.
`aim`, `speed`, `slider_factor`, `speed_note_count` are null for maps where the API returns only star rating.
`cs/ar/od/bpm/length_s` are computed by us from the nomod map stats (`mods.adjust`), not returned by the attributes API. BPM is a map-level tempo value and does not establish local tempo or tapping rate; beatmaps may change tempo. Total length includes breaks and does not establish continuous strain. The adjustment handles EZ, HR, DA and constant DT/HT rates only; other mods or settings may make these estimates inaccurate. Variable-speed mods WU, WD and AS have null AR, OD, BPM and length because a single rate does not describe the play. Older caches for these combinations are ignored.

Manual mod input rejects combinations with multiple speed-changing mods or more than one of EZ, HR and DA. This is a partial compatibility check, not a complete list of incompatible mods or a guarantee that a combination is playable or ranked. Recorded score mods are preserved as reported by the API.

For manual custom-rate requests, DT/NC accept 1.01 to 2.0 and HT/DC accept 0.5 to 0.99, matching the documented lazer mod ranges. This does not establish ranked or pp eligibility, and recorded score settings are retained separately.

## session.json
```json
{"session_id": "2026-11-21_2000",
 "started_at": "ISO8601 with offset", "ended_at": "ISO8601 or null while open",
 "before": {"sleep_h": 7, "energy_1_5": 3, "hand_pain_0_10": 0, "motivation_1_5": 4},
 "after":  {"duration_min": 60, "fatigue_1_5": 3, "hand_pain_0_10": 0,
            "enjoyment_1_5": 4, "felt_hard": "free text", "notes": "free text"},
 "followed_plan": "yes|partly|no",
 "setup": {"input": "tablet", "tablet": "Example tablet", "area_mm": [60,40],
           "area_center_mm": [75.5,45], "area_offset_mm": null,
           "area_rotation_deg": 0, "driver": "OpenTabletDriver",
           "keypad": "Example keypad", "rapid_trigger": null, "actuation": null,
           "fps": 480, "client": "lazer", "changed_since_last": false}}
```
`session start` reads `coach/player.md` and saves a setup snapshot. Edit the profile to change future sessions. For example, `- tablet area: 60 × 40 mm, center (75.5, 45) mm, rotation 0°` supplies dimensions, center coordinates and rotation. Blank setup fields are null. If the profile is missing, the CLI reuses the last recorded setup when available.
`setup.changed_since_last` is true when known recorded values differ, and otherwise null unless the player explicitly reports a change or confirms the setup is the same. Matching saved fields cannot rule out unrecorded or stale settings. `--setup-changed` and `--setup-same` record the player's report and override the comparison; earlier sessions may contain automatically inferred false values from older versions. Unknown `before` and `after` fields are null.

Optional `rapid_trigger` and `actuation` are free-text player-reported settings copied from the player profile at session start. Null means unknown, not disabled. Older sessions may omit them. They describe reported configuration, not measured timing or proof of causation.
`after.duration_min` is computed at `session end`. Edit fields afterwards only with `osucoach session update`.
Energy, motivation, fatigue and enjoyment use integer values from 1 to 5. Pain uses integers from 0 to 10. Sleep hours must be finite and non-negative. Invalid updates are rejected before saving.
Optional `blocks` is an ordered array of `{ "label": "focus", "started_at": "ISO8601 with offset" }` markers created by `session block`. Older sessions may omit it. Statistics assign scores by score end time, including crossing plays, and count plays before the first marker separately.
Each block summary also counts assigned plays that started before the block (`crossing_start`) and those with no known start time (`unknown_start`). Crossing and unknown-start plays remain in the block totals, so use caution when interpreting block-specific performance. Each block also contains per-beatmap and mod-setting `maps` summaries with score IDs, pass and accuracy sample counts, hit-result coverage, and per-group crossing/unknown-start counts. These use the same end-time assignment and can contain different source clients or scoring rules within one group. `fully_contained` within each block and map group summarizes only assigned scores with known start times at or after that block's start; the score end is before the next marker when one exists. For a closed session, `fully_contained` excludes scores ending after the session end; `after_session_end` counts those scores in the ordinary block and map totals. While the session is open, this count is null and the final block has no closing boundary. Excluded and missing plays remain unknown, and fully contained does not prove comparable conditions or complete coverage.
`session end` saves the end time and feedback before syncing scores. If sync fails, the session remains current and retrying `session end` keeps the original end time and feedback. Run it again promptly to recover scores.
`session end --offline` closes the saved session without fetching. Sync it later with `fetch recent --session ID` and `fetch beatmaps --session ID`. Existing scores and sync metadata, if any, are retained and may be incomplete.
`history BEATMAP_ID` includes matching saved `best_scores`, `best_sync`, the workspace account binding and IDs duplicated between saved best and session scores. A null best list means no best file; an empty list means no matching saved top scores, not no prior plays. Session duplicates remain in their original entries; `session_duplicate_scores` lists IDs occurring in multiple saved sessions and `unique_recorded_session_score_count` counts distinct IDs found in the available session files. Neither number establishes complete play history. Best-score and session coverage and conditions can differ.

`maps` candidates include distinct saved `recorded_score_ids` and `recorded_score_count` for the displayed map, mods and settings. Mod order is ignored but settings are kept distinct. `recorded_history` exposes the account binding, best-file presence and sync metadata, and sessions missing score files. These IDs cover only saved records, including selective best scores and possibly incomplete or overlapping sessions; missing IDs are not counted. Matching mods and settings do not establish matching client, setup, scoring rules or complete play history. Use `history` and session context to inspect conditions and provenance before comparing outcomes.

`maps` excludes records without valid cached star ratings and reports exclusion counts in `excluded_score_records`. Invalid BPM, AR or length values are shown as null and excluded when that field is filtered. Counts describe encountered saved records, not distinct scores or maps, and can include duplicates across sources. They do not measure complete history.

Each matching history session includes its start and end timestamps and block markers to help interpret the score times. An open session has a null end; an empty block list means no markers were saved. A score ending in a block may have started earlier, and a start-only timestamp or an older score without timestamp provenance cannot establish its end-time assignment. See session block summaries for containment and coverage limitations.

`scores [ID]` returns the saved session score rows and sync/account metadata; its `scores` field is null if no score file exists and an empty array if one exists with no recorded scores. `stats` returns an error when `scores.json` is missing. An existing empty array reports zero recorded plays, subject to the same coverage limitations as any fetched data.
A session's scores are those with `created_at` from `started_at` to `ended_at` + 5 min. If a new session starts within that grace period, the same score can appear in both sessions. `context` reports IDs present in both saved files in `overlapping_score_ids`, keyed by overlapping session ID. Null means one or both files are missing; an empty array does not establish complete coverage. Compare score IDs before combining statistics across sessions.

Nonempty session stats include `session_boundary_evidence` when the session start is known: counts of scores known to have started before the session, scores with unknown starts, scores with explicit end times after the session, and scores with unknown end-time provenance. The after-end count is null for an open session. Counts can overlap and do not remove scores from totals. The five-minute grace period, missing scores and legacy timestamps still limit attribution.

`scores_sync.json` records `fetched_at`, the number of scores returned by the API, whether the recent-score limit was reached, and the oldest and newest returned timestamps. It describes the most recent request, while `scores.json` merges scores from prior requests. osu!web currently limits recent-score results to 100 total. A full response indicates potential truncation. A smaller response also does not prove complete coverage because the API's recent history is limited and scores may be unavailable. Older metadata may incorrectly report the limit as not reached after a full first page.
`limit_reached_ever` remains true after an observed full response, including identifiable older metadata with at least 100 returned scores. It covers only recorded sync history and cannot recover unrecorded earlier gaps.

First and last 20-minute statistics require at least 40 minutes between the earliest and latest recorded score timestamps. The first window excludes its endpoint and the last includes its start, preventing overlap at exactly 40 minutes. These windows follow recorded score end times, not session start and end.

Stats expose `first_recorded_attempts` and `later_recorded_attempts`, grouped by beatmap ID within the saved session scores regardless of mods. These are first recorded attempts in that session, not necessarily first-ever or sightread plays. Missing scores can change the grouping. They describe performance under different potentially non-comparable conditions and do not establish learning or transfer.

## replay_stats.json (optional, one per replay)
```json
{"replay_file": "x.osr", "beatmap_id": 0, "mods": [],
 "ur": 0.0, "mean_error_ms": 0.0,
 "misses": [{"t_ms": 0, "object_index": 0, "kind": "aim|tap|slider_break|unknown",
             "cursor_dist_r": 0.0}],
 "early_late_by_section": [{"t_start_ms": 0, "t_end_ms": 0, "mean_error_ms": 0.0, "ur": 0.0}]}
```
This is a proposed external format, not produced or validated by the CLI. A replay stores sampled input; deriving object judgements and timing requires the matching beatmap, client rules, mods and correct replay interpretation. A click outside a circle or outside a hit window does not by itself prove the cause of a miss. Treat classifications as hypotheses with documented provenance and uncertainty; do not assume the example labels are verified diagnoses.
