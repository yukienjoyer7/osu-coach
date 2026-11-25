# osu-coach MVP plan

> Historical design plan. The CLI, coaching guidance and scope have evolved; use [README.md](README.md), `osucoach guide` and `osucoach schema` for current behavior. This plan is not evidence of present-day live API compatibility or elite coaching effectiveness.

> Status 2026-11-20: steps 1-6 implemented and verified against the live API (17 offline tests in `tests/`). Step 7 (first real loop) is the player's. Implementation notes that differ from this plan: the attributes endpoint returns no AR/OD, so `mods.adjust` computes them; map meta comes free from score objects (`data/beatmaps/_meta/`); `session update` was added so the agent never hand-edits `data/`.

Goal: an agent living in the repo that coaches osu!standard sessions (pre / live / post) using data it fetches itself through a CLI. Harness-agnostic (Claude Code, Codex, anything that reads `AGENTS.md`).

Out of scope for MVP: replay parsing, live game state (tosu), UI, RAG over research/community corpus.

---

## 0. Decisions (locked for MVP)

| Item | Choice |
|---|---|
| Language | Python 3.11+ |
| CLI | `typer` |
| HTTP | `httpx` |
| Difficulty calc | osu! API `POST /beatmaps/{id}/attributes` (rosu-pp-py later, for offline) |
| Storage | plain JSON files per `DATA_CONTRACT.md` (no DB yet) |
| Auth | osu! OAuth client credentials, scope `public` |
| Secrets | `.env` (gitignored), never printed |
| Coach model | Opus 5.5 via the harness, rules in the skill |

---

## 1. Repo layout

```
osu-coach/
  AGENTS.md                      # harness-agnostic entry point
  CLAUDE.md                      # one line: "Read AGENTS.md"
  DATA_CONTRACT.md
  plan.md
  .env.example                   # OSU_CLIENT_ID, OSU_CLIENT_SECRET, OSU_USER_ID=36136377
  pyproject.toml
  .claude/skills/osu-coach/SKILL.md
  src/osucoach/
    __init__.py
    cli.py                       # typer app
    api.py                       # token + GET/POST + cache + rate limit
    fetch.py                     # profile, best, recent, beatmap, threshold
    session.py                   # start / end / current
    stats.py                     # session summary numbers
    paths.py                     # data/ and coach/ locations
  data/                          # gitignored or committed, your call
  coach/
    player.md
    journal.md
    plans/  reviews/
  tests/
```

---

## 2. CLI spec

All commands write per the contract and print a short human summary. `--json` prints machine output for the agent.

```
osucoach auth check                         # token works, prints user + pp
osucoach fetch profile                      # data/profile/YYYY-MM-DD.json
osucoach fetch best                         # data/best.json (top 100)
osucoach fetch threshold                    # pp of rank #10,000 -> into today's profile
osucoach fetch recent [--session ID]        # scores incl. fails -> session scores.json
osucoach fetch beatmap ID [--mods HD,DT]    # data/beatmaps/ID_MODS.json (cached)
osucoach session start                      # creates data/sessions/<id>/session.json, sets current
osucoach session end                        # stamps end time, runs fetch recent + beatmaps
osucoach session current                    # prints current session id or none
osucoach stats [ID]                         # summary of a session (default: current/last)
osucoach gap                                # current pp, threshold, gap, plays-needed table
```

Behaviour rules:
- Token cached in memory per run (client credentials token lasts ~1 day; fine to re-request).
- Rate limit: max 60 requests/min, sleep if exceeded.
- Beatmap attribute files are cached forever (key = id + mods).
- `fetch recent --session` keeps only scores with `created_at >= session start` and computes `retry_index`.
- Non-zero exit code + one-line error on failure. Never print secrets.

---

## 3. API notes

Base: `https://osu.ppy.sh/api/v2`, token: `POST https://osu.ppy.sh/oauth/token`
(`grant_type=client_credentials`, `scope=public`).

| Need | Endpoint |
|---|---|
| profile | `GET /users/{id}/osu` |
| best | `GET /users/{id}/scores/best?mode=osu&limit=100` |
| recent | `GET /users/{id}/scores/recent?mode=osu&include_fails=1&limit=100` (last ~24h only) |
| beatmap attrs | `POST /beatmaps/{id}/attributes` body `{"mods": [...], "ruleset": "osu"}` |
| beatmap meta (AR/OD/CS/BPM/length) | `GET /beatmaps/{id}` (apply mod math for DT/HR/EZ/HT yourself) |
| #10,000 pp | `GET /rankings/osu/performance?cursor[page]=200` -> last entry |

Check early: score object shape differs by `x-api-version` header (newer format uses `statistics.great/ok/meh/miss`). Pick one and map to `n300/n100/n50/miss` in one place.

---

## 4. Build order (tonight, ~3-4h)

### Step 1: skeleton + auth (30 min)
- [ ] Create osu! OAuth app (account settings -> OAuth), fill `.env`
- [ ] `pyproject.toml` with `osucoach` entry point
- [ ] `api.py`: token, `get()`, `post()`, rate limit
- [ ] `osucoach auth check`
- **Done when:** prints `crtal7 | 8,439 pp | #18,415`

### Step 2: profile, best, threshold, gap (45 min)
- [ ] `fetch profile`, `fetch best`, `fetch threshold`
- [ ] `gap`: threshold - pp, plus plays needed at 500/550/600pp
  (weighted sum without bonus `W`, new play `p` at top: `W' = p + 0.95 * W`)
- **Done when:** `osucoach gap` shows ~1,170 pp gap and the table

### Step 3: beatmap attributes (30 min)
- [ ] `fetch beatmap ID --mods` writes the contract schema, cached
- [ ] Run it over all 100 entries of best.json once
- **Done when:** 100 files in `data/beatmaps/`, second run makes 0 API calls

### Step 4: sessions + recent (45 min)
- [ ] `session start/end/current`; `session.json` with `before`/`after`/`setup` (prompt for before values on start, after values on end, or leave nulls)
- [ ] `fetch recent --session`, `retry_index`
- [ ] `session end` = stamp + fetch recent + fetch beatmaps for those scores
- **Done when:** a 3-map test session produces `scores.json` with correct retry counts

### Step 5: stats (30 min)
- [ ] plays, passes, pass rate, acc mean by map, retries per map, best pp, avg SR passed vs failed, first-half vs second-half acc
- **Done when:** `osucoach stats` fits on one screen

### Step 6: agent wiring (30 min)
- [ ] Write `AGENTS.md` (section 5)
- [ ] Put the skill at `.claude/skills/osu-coach/SKILL.md`, add the `live` mode (section 6)
- [ ] Create `coach/player.md` from the skill template, set `client: stable|lazer`
- **Done when:** in a fresh harness, "pre" produces a plan file without you explaining anything

### Step 7: first real loop
- [ ] `pre` -> play 45-60 min -> `session end` -> `post`
- [ ] Note what was missing or annoying in `coach/journal.md` under `## dev notes`
- That list is the next sprint.

---

## 5. AGENTS.md (draft)

```md
# osu-coach

You are an osu!standard coach for the player in coach/player.md.
Detailed rules: .claude/skills/osu-coach/SKILL.md (read it before coaching).
Data schemas: DATA_CONTRACT.md.

## Tools (run in shell)
osucoach session start | end | current
osucoach fetch profile | best | threshold | recent [--session ID] | beatmap ID [--mods X]
osucoach stats [ID]
osucoach gap
Add --json when you need to parse output.

## Modes
- pre: plan the next session -> coach/plans/<id>.md
- live: player is mid-session, answer short
- post: review -> coach/reviews/<id>.md, append coach/journal.md
Infer the mode from the request and `osucoach session current`.

## Rules
- Fetch before you reason if data is older than the question needs.
- Never read or print .env.
- Do not edit data/ by hand; only the CLI writes there.
- Brief, direct, no em-dashes.
```

## 6. Skill addition: `live` mode

Add to SKILL.md under Modes:

```md
### live: mid-session question
Triggered when a session is open (`osucoach session current`) and the user asks something.
- Run `osucoach fetch recent --session <id>` then `osucoach stats <id> --json`.
- Answer in 3-5 lines. One actionable thing.
- Only use scores from this session plus player.md.
- Do not rewrite the plan unless asked.
- If acc or pass rate in the last 20 min dropped clearly vs the first 20 min, suggest a 5 min break.
- If the user mentions pain, tell them to stop for today.
```

---

## 7. Sprint 2 candidates (pick after the first loop)

- Replay parsing (`osrparse`): UR, early/late, aim vs tap misses -> `replay_stats.json`
- `rosu-pp-py` for offline attributes and "what pp would 98% on this map give"
- Map finder: query beatmaps by SR/BPM/AR/length for plan criteria
- Weekly report: trend across sessions, hypothesis verdicts
- tosu websocket for semi-live data
- Population scraping for the research question
