# osu-coach data contract (MVP)

The fetcher writes; the coach only reads `data/` and writes `coach/`.

```
data/
  profile/YYYY-MM-DD.json          # snapshot per fetch
  best.json                        # top 100, refreshed each fetch
  best_sync.json                   # timestamp and count of last successful best-score fetch
  map_tags/<beatmap_id>.json       # user-supplied, mod-independent labels
  beatmaps/<beatmap_id>_<mods>.json   # <mods> = difficulty-affecting mods only: NM, DT, HR, DTHR, DT-sp1.3 (NC=DT, DC=HT; HD/FL/etc ignored)
  beatmaps/_meta/<beatmap_id>.json    # raw nomod CS/AR/OD/BPM/length/counts captured from score objects (cache)
  current_session                  # id of the open session, absent when none
  sessions/<session_id>/
    session.json                   # created/updated only via the CLI (see session.json below)
    scores.json                    # scores set during the session, incl. fails
    scores_sync.json               # most recent successful recent-score request metadata
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
- `retry_index`: Nth attempt of that beatmap within this session (compute from created_at order).
- `pp` may be null for fails/unranked; keep the row anyway. `weight_pct` is only set for best.json, null for session scores.
- `created_at` is the score's `ended_at` (UTC). `map_max_combo` is null until the beatmap file exists.
- `started_at` is the API's score start time when available, otherwise null. Older saved scores may omit it.
- `mod_settings` (optional): present only when a mod has settings, e.g. `{"DT": {"speed_change": 1.3}}`.
- Lazer score format: `n300/n100/n50/miss` = `statistics.great/ok/meh/miss`.
- When the statistics dictionary is absent, those counts are null. Within a present dictionary, omitted hit results are treated as zero. Scores saved by older versions may contain zeros that cannot be distinguished from missing source statistics without refetching.
- Session and map stats include `hit_results` with totals for scores that have all four counts, plus the number of scores covered and the total scores. Null totals mean no complete counts were available. These aggregates include fails and retries, which can have different played lengths. Miss counts do not identify aim or tapping causes.
- `acc_passed` in session, block, window and map stats is the unweighted mean accuracy of passed scores with known accuracy. `acc_passed_samples` gives its denominator. Failed plays and passed scores with unknown accuracy are excluded.
- `avg_sr_passed` and `avg_sr_failed` are unweighted means over recorded scores with cached star ratings. Their corresponding `_samples` fields give the number included. Missing or stale beatmap attributes can limit comparisons.

## beatmaps/<beatmap_id>_<mods>.json
```json
{"beatmap_id": 0, "mods": ["DT"], "star_rating": 0.0,
 "aim": 0.0, "speed": 0.0, "slider_factor": 0.0, "speed_note_count": 0.0,
 "ar": 0.0, "od": 0.0, "cs": 0.0, "bpm": 0.0, "length_s": 0,
 "n_circles": 0, "n_sliders": 0, "n_spinners": 0}
```
Values are mod-adjusted (DT changes AR/OD/BPM/length). Extra field `max_combo` (null if unknown).
New attribute files include `fetched_at`. Older files may omit it. `fetch beatmap ID --mods X --refresh` requests fresh map metadata and difficulty attributes for that mod combination. Other cached mod combinations retain their previous values until refreshed. Algorithm changes can make old cached ratings stale.
`aim`, `speed`, `slider_factor`, `speed_note_count` are null for maps where the API returns only star rating.
`cs/ar/od/bpm/length_s` are computed by us from the nomod map stats (`mods.adjust`), not returned by the API.

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
           "keypad": "Example keypad",
           "fps": 480, "client": "lazer", "changed_since_last": false}}
```
`session start` reads `coach/player.md` and saves a setup snapshot. Edit the profile to change future sessions. For example, `- tablet area: 60 × 40 mm, center (75.5, 45) mm, rotation 0°` supplies dimensions, center coordinates and rotation. Blank setup fields are null. If the profile is missing, the CLI reuses the last recorded setup when available.
`setup.changed_since_last` is true for a difference between known values, false when comparable known values match and no field is known on only one side, and null when there is no comparable baseline or a field is known on only one side. Two null fields give no evidence of a change. `--setup-changed` and `--setup-same` override the comparison. Unknown `before` and `after` fields are null.
`after.duration_min` is computed at `session end`. Edit fields afterwards only with `osucoach session update`.
Energy, motivation, fatigue and enjoyment use integer values from 1 to 5. Pain uses integers from 0 to 10. Sleep hours must be finite and non-negative. Invalid updates are rejected before saving.
Optional `blocks` is an ordered array of `{ "label": "focus", "started_at": "ISO8601 with offset" }` markers created by `session block`. Older sessions may omit it. Statistics assign scores by score end time, including crossing plays, and count plays before the first marker separately.
Each block summary also counts assigned plays that started before the block (`crossing_start`) and those with no known start time (`unknown_start`). Crossing and unknown-start plays remain in the block totals, so use caution when interpreting block-specific performance.
`session end` saves the end time and feedback before syncing scores. If sync fails, the session remains current and retrying `session end` keeps the original end time and feedback. Run it again promptly to recover scores.
`session end --offline` closes the saved session without fetching. Sync it later with `fetch recent --session ID` and `fetch beatmaps --session ID`. Existing scores and sync metadata, if any, are retained and may be incomplete.
`stats` returns an error when `scores.json` is missing. An existing empty array reports zero recorded plays, subject to the same coverage limitations as any fetched data.
A session's scores are those with `created_at` from `started_at` to `ended_at` + 5 min. If a new session starts within that grace period, the same score can appear in both sessions. Compare score IDs before combining statistics across sessions.

`scores_sync.json` records `fetched_at`, the number of scores returned by the API, whether the recent-score limit was reached, and the oldest and newest returned timestamps. It describes the most recent request, while `scores.json` merges scores from prior requests. osu!web currently limits recent-score results to 100 total. A full response indicates potential truncation. A smaller response also does not prove complete coverage because the API's recent history is limited and scores may be unavailable. Older metadata may incorrectly report the limit as not reached after a full first page.

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
- `cursor_dist_r`: cursor distance from circle center at the click, in radii. >1 means an aim miss.
- `kind`: aim if cursor_dist_r > 1, tap if inside the circle but outside the hit window.
