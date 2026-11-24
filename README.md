# osu-coach

Agent-in-repo osu!standard coach. The `osucoach` CLI fetches data from the osu! API v2; any agent harness that reads `AGENTS.md` does the coaching.

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
# play; ask the agent anything mid-session (live mode)
osucoach session end        # then ask the agent: "post"
```

See `plan.md` for the build plan and `DATA_CONTRACT.md` for schemas.

`osucoach context [ID]` outputs saved player, profile, session, plan, review and score statistics as JSON for any tool-capable agent. It works offline and reports missing files as null. Profile fetch time and local file modification times help assess freshness, but do not prove complete score coverage. Fetch fresh data when needed.

## Development (maintainer notes)
- Python 3.11+ (developed on 3.12 from python.org / `winget install Python.Python.3.12`; the msys2 Python lacks wheels for pydantic).
- Run the CLI as `.venv/Scripts/osucoach`; tests with `.venv/Scripts/python -m pytest`.
- Layout (`src/osucoach/`): `api.py` (HTTP, token, rate limit; only module that touches the network), `fetch.py` (API -> data/), `scores.py` (the only place that knows the score JSON shape), `mods.py` (mod parsing, cache keys, AR/OD/CS/BPM maths), `gap.py` (weighted pp maths), `session.py`, `stats.py` (pure functions), `store.py` (JSON IO, only module that writes files), `paths.py`, `cli.py` (thin typer layer).
- Score format is the lazer one (`x-api-version` header in `api.py`); map `statistics.*` to `n300/n100/n50/miss` in one place only.
- Never print or log `.env` values. Errors are one line, non-zero exit.
- `data/` is written only by the CLI; `coach/` is written by the agent.
