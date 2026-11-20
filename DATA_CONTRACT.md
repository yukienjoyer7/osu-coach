# osu-coach data contract (MVP)

The fetcher writes; the coach only reads `data/` and writes `coach/`.

```
data/
  profile/YYYY-MM-DD.json          # snapshot per fetch
  best.json                        # top 100, refreshed each fetch
  beatmaps/<beatmap_id>_<mods>.json   # <mods> = difficulty-affecting mods only: NM, DT, HR, DTHR, DT-sp1.3 (NC=DT, DC=HT; HD/FL/etc ignored)
  beatmaps/_meta/<beatmap_id>.json    # raw nomod CS/AR/OD/BPM/length/counts captured from score objects (cache)
  current_session                  # id of the open session, absent when none
  sessions/<session_id>/
    session.json                   # created/updated only via the CLI (see session.json below)
    scores.json                    # scores set during the session, incl. fails
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
- `mod_settings` (optional): present only when a mod has settings, e.g. `{"DT": {"speed_change": 1.3}}`.
- Lazer score format: `n300/n100/n50/miss` = `statistics.great/ok/meh/miss`.

## beatmaps/<beatmap_id>_<mods>.json
```json
{"beatmap_id": 0, "mods": ["DT"], "star_rating": 0.0,
 "aim": 0.0, "speed": 0.0, "slider_factor": 0.0, "speed_note_count": 0.0,
 "ar": 0.0, "od": 0.0, "cs": 0.0, "bpm": 0.0, "length_s": 0,
 "n_circles": 0, "n_sliders": 0, "n_spinners": 0}
```
Values are mod-adjusted (DT changes AR/OD/BPM/length). Extra field `max_combo` (null if unknown).
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
 "setup": {"input": "tablet", "tablet": "Wacom CTL-672", "area_mm": [0,0],
           "area_offset_mm": [0,0], "driver": "", "keypad": "SayoDevice O3C",
           "fps": 800, "client": "lazer", "changed_since_last": false}}
```
`session start` copies `setup` from the previous session (changed_since_last=false); unknown `before`/`after` fields are null.
`after.duration_min` is computed at `session end`. Edit fields afterwards only with `osucoach session update`.
A session's scores are those with `created_at` from `started_at` to `ended_at` + 5 min.

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
