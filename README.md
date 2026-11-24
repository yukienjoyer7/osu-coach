# osu-coach

Agent-in-repo osu!standard coach. The `osucoach` CLI fetches data from the osu! API v2. Agents can use `AGENTS.md` and the shared coaching playbook to coach the player. Run `osucoach guide` to print the playbook, including from an installed package. The Claude skill points to the same playbook.

## Setup
```
cp .env.example .env        # fill OSU_CLIENT_ID / OSU_CLIENT_SECRET / OSU_USER_ID
cp coach/player.example.md coach/player.md      # then edit; personal, gitignored
cp coach/journal.example.md coach/journal.md
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; use .venv/bin/ on Linux/macOS
.venv/Scripts/osucoach auth check
```

## Session loop
```
osucoach session start      # then ask the agent: "pre"
osucoach session block warmup  # mark each practice block as it starts
# play; ask the agent anything mid-session (live mode)
osucoach session end        # then ask the agent: "post"
```

If the API is unavailable, `osucoach session end --offline` saves feedback and closes the session without fetching. Once connected, run `osucoach fetch recent --session ID` followed by `osucoach fetch beatmaps --session ID`. Fetch promptly because the API retains only a limited recent history.

See `plan.md` for the build plan. Run `osucoach schema` for the data contract, including from an installed package.

`osucoach context [ID]` outputs saved player, profile, session, plan, review, up to three previous sessions with plans, reviews, conditions, score statistics and sync metadata as JSON for any tool-capable agent. It works offline and reports missing files as null. For a selected session, journal sections with later session IDs or no recognizable session ID are excluded; the omission count is reported. The last three eligible sections are ordered by session ID even if reviews were appended out of order. Profile and best scores are still the latest available snapshots, not historical snapshots. For overlapping session windows, `overlapping_score_ids` identifies saved duplicate scores; null means a score file is missing. Profile fetch time and local file modification times help assess freshness, but do not prove complete score coverage. Fetch fresh data when needed.

`osucoach scores [ID]` provides the saved score rows, session boundaries, account binding and sync metadata as JSON. A null score list means no score file has been fetched; an empty list means no plays were recorded in the saved file, not that coverage is complete.

`osucoach history BEATMAP_ID` shows saved scores for a map across sessions, including mods, setup snapshots and fetch metadata. It includes matching saved top scores and their fetch metadata, lists sessions with missing score files, and reports duplicate score IDs when session windows overlap while preserving the original records. The top-score list is selective, not a complete play history. Compare conditions, including client and Classic mod settings when known, and deduplicate IDs before interpreting changes. Newly fetched scores retain the API's legacy score ID and build ID when present; their absence does not prove a client.

`osucoach maps --min-sr 5 --max-sr 6 --min-bpm 170 --max-bpm 210 --min-ar 9 --max-length 180` finds recorded maps with cached attributes. Length is in seconds. Results only cover stored scores with cached map attributes. Pattern type and ranked status are unknown and must be verified separately. BPM is a map-level tempo, not a guarantee of tapping speed, and total length includes breaks.

`osucoach discover 'stars>=5 stars<6 bpm>=170 bpm<=210'` requests candidate beatmapsets using the osu! API. Pass a returned `cursor_string` as `--cursor` for another page. Optional `--min-sr` and `--max-sr` locally filter a separate `ranked_osu_candidates` list using the returned difficulty's mode, status and nomod rating. Counts disclose missing difficulty lists or required fields. `--tag` filters on player-supplied labels, which remain subjective and apply across mods. Candidates retain `lazer_only` when supplied; null means client compatibility is unknown. The server may ignore advanced filters depending on search permissions; inspect each returned difficulty, mode and ranked status, and verify pattern suitability and mod-adjusted attributes. Each candidate includes a deduplicated count of saved score IDs, alongside missing-file and account-provenance metadata. Zero does not establish that the player has never played or seen a map.

`osucoach map-search 'stars>=5 stars<6 bpm>=170 bpm<=210'` builds a link to the official ranked osu!standard beatmap listing for discovering additional maps. The website filters base map attributes, so verify individual difficulties, mods and pattern suitability before recommending them.

Use `osucoach map-attributes 123,456 --mods DT --rate 1.3` to fetch or reuse attributes for up to 25 candidate difficulties. Results identify errors per map and do not establish ranked status or pattern type.

Use two-character mod acronyms such as `HDDT` or `HD,DT`; `NM` must stand alone. Use `osucoach fetch beatmap 123 --mods DT --rate 1.3 --refresh` to refresh a custom-rate difficulty cache. Difficulty Adjust values in the standard 0–10 range can be specified with `--mods DA --ar 9 --od 8 --cs 4`. Extended-limit settings are not supported by these options.

`osucoach map-tag 123 --tags jumps,bursts` replaces the user-supplied labels for beatmap 123. Find labeled candidates with `osucoach maps --tag jumps`. Labels apply to the beatmap across all mods, are subjective, and do not verify ranked status. An empty `--tags` value clears them.

Use `osucoach fetch threshold --rank 100` and `osucoach gap --rank 100 --pp 1000,1200,1400` to model a rank target. The calculation models only the top 100 and holds the remaining contribution, bonus pp and the rank threshold constant. It assumes distinct new scores and does not model changes below the top 100. Fetch times help assess whether inputs are comparable. It is a scenario, not a forecast.

For manual, harness-independent checks of coaching responses, see [the evaluation cases](evals/README.md). They are development examples, not evidence of elite coaching effectiveness.

Use `osucoach session block LABEL` when a new block begins. JSON stats include each marked block, per-map and mod summaries with score IDs, and a count of plays before the first marker. Scores are assigned by their recorded end time, so a play crossing a marker belongs to the later block. Block metrics use recorded scores and are subject to score coverage limitations.

Player data fetched after the first successful player fetch is bound to that workspace’s configured osu! user. Use a separate workspace for another account. `osucoach context` reports whether older files predate the binding; their origin remains unverified.

The workspace defaults to the current working directory. Set `OSUCOACH_HOME` to an absolute workspace directory to use it from elsewhere, including when the package is installed separately. The CLI reads `coach/` and `.env` and writes `data/` there. `osucoach context` reports the resolved workspace path so an agent can locate coaching files even when started elsewhere. Run `osucoach init` to create missing player and journal templates and plan/review directories without overwriting existing files; edit the player file before coaching. Run `osucoach guide` to supply the playbook to any agent. For example, on Linux or macOS use `export OSUCOACH_HOME=/path/to/workspace` before invoking the CLI.

## Development (maintainer notes)
- Python 3.11+ (developed on 3.12 from python.org / `winget install Python.Python.3.12`; the msys2 Python lacks wheels for pydantic).
- Run the CLI as `.venv/Scripts/osucoach`; tests with `.venv/Scripts/python -m pytest`.
- Layout (`src/osucoach/`): `api.py` (HTTP, token, rate limit; only module that touches the network), `fetch.py` (API -> data/), `scores.py` (the only place that knows the score JSON shape), `mods.py` (mod parsing, cache keys, AR/OD/CS/BPM maths), `gap.py` (weighted pp maths), `session.py`, `stats.py` (pure functions), `store.py` (JSON IO, only module that writes files), `paths.py`, `cli.py` (thin typer layer).
- Score format is the lazer one (`x-api-version` header in `api.py`); map `statistics.*` to `n300/n100/n50/miss` in one place only.
- Never print or log `.env` values. Errors are one line, non-zero exit.
- `data/` is written only by the CLI; `coach/` is written by the agent.
