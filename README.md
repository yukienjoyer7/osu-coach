# osu-coach

Agent-in-repo osu!standard coach. The `osucoach` CLI fetches data from the osu! API v2. Agents can use `AGENTS.md` and the shared `COACHING.md` playbook to coach the player. The Claude skill points to the same playbook.

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

See `plan.md` for the build plan and `DATA_CONTRACT.md` for schemas.

`osucoach context [ID]` outputs saved player, profile, session, plan, review, up to three previous session reviews and score statistics as JSON for any tool-capable agent. It works offline and reports missing files as null. Profile fetch time and local file modification times help assess freshness, but do not prove complete score coverage. Fetch fresh data when needed.

Use `osucoach fetch threshold --rank 100` and `osucoach gap --rank 100 --pp 1000,1200,1400` to model a rank target. The calculation holds bonus pp and the rank threshold constant and assumes distinct new scores. It is a scenario, not a forecast.

Use `osucoach session block LABEL` when a new block begins. JSON stats include each marked block and a count of plays before the first marker. Scores are assigned by their recorded end time, so a play crossing a marker belongs to the later block. Block metrics use recorded scores and are subject to score coverage limitations.

Set `OSUCOACH_HOME` to an absolute workspace directory when the package is installed separately from the player's files. The CLI reads `coach/` and `.env` and writes `data/` under that directory. The default remains the repository root for editable installations. For example, on Linux or macOS use `export OSUCOACH_HOME=/path/to/workspace` before invoking the CLI. Keep the coaching instructions accessible to your agent in that workspace.

## Development (maintainer notes)
- Python 3.11+ (developed on 3.12 from python.org / `winget install Python.Python.3.12`; the msys2 Python lacks wheels for pydantic).
- Run the CLI as `.venv/Scripts/osucoach`; tests with `.venv/Scripts/python -m pytest`.
- Layout (`src/osucoach/`): `api.py` (HTTP, token, rate limit; only module that touches the network), `fetch.py` (API -> data/), `scores.py` (the only place that knows the score JSON shape), `mods.py` (mod parsing, cache keys, AR/OD/CS/BPM maths), `gap.py` (weighted pp maths), `session.py`, `stats.py` (pure functions), `store.py` (JSON IO, only module that writes files), `paths.py`, `cli.py` (thin typer layer).
- Score format is the lazer one (`x-api-version` header in `api.py`); map `statistics.*` to `n300/n100/n50/miss` in one place only.
- Never print or log `.env` values. Errors are one line, non-zero exit.
- `data/` is written only by the CLI; `coach/` is written by the agent.
