"""Session summary numbers. Pure functions over scores.json rows (plus a map lookup)."""
from collections import Counter, defaultdict
import json
import math
from statistics import mean

from .store import parse_ts

WINDOW_MIN = 20


def _valid_accuracy(row):
    value = row.get("accuracy")
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _acc(rows: list[dict]) -> float | None:
    vals = [r["accuracy"] for r in rows if r.get("passed") is True and _valid_accuracy(r)]
    return round(100 * mean(vals), 2) if vals else None


def _pass_rate(rows: list[dict]) -> float | None:
    known = [r for r in rows if type(r.get("passed")) is bool]
    return round(100 * sum(r["passed"] for r in known) / len(known), 1) if known else None


def _block(rows: list[dict]) -> dict:
    return {"plays": len(rows), "pass_rate": _pass_rate(rows),
            "pass_rate_samples": sum(type(row.get("passed")) is bool for row in rows), "acc_passed": _acc(rows),
            "acc_passed_samples": sum(row.get("passed") is True and _valid_accuracy(row) for row in rows)}


def _hit_results(rows: list[dict]) -> dict:
    fields = ("n300", "n100", "n50", "miss")
    known = [row for row in rows if all(type(row.get(field)) is int and row[field] >= 0 for field in fields)]
    return {"scores_with_counts": len(known), "scores_total": len(rows),
            "totals": {field: sum(row[field] for row in known) for field in fields} if known else None}


def _condition_key(row):
    settings = json.dumps(row.get("mod_settings") or {}, sort_keys=True, separators=(",", ":"))
    return row["beatmap_id"], tuple(sorted(row["mods"])), settings


def _contained(rows, start, session_end):
    contained = [row for row in rows if row.get("started_at") and parse_ts(row["started_at"]) >= start
                 and (session_end is None or parse_ts(row["created_at"]) <= session_end)]
    return {**_block(contained), "passes": sum(row.get("passed") is True for row in contained),
            "score_ids": [row["score_id"] for row in contained],
            "hit_results": _hit_results(contained)}


def _block_maps(rows, start, session_end):
    groups = defaultdict(list)
    for row in rows:
        groups[_condition_key(row)].append(row)
    summaries = []
    for (beatmap_id, mods, settings), group in groups.items():
        summaries.append({
            "beatmap_id": beatmap_id, "title": group[0]["title"], "version": group[0]["version"],
            "mods": list(mods), "mod_settings": json.loads(settings),
            "score_ids": [row["score_id"] for row in group],
            **_block(group), "passes": sum(row.get("passed") is True for row in group),
            "hit_results": _hit_results(group),
            "crossing_start": sum(parse_ts(row["started_at"]) < start for row in group if row.get("started_at")),
            "unknown_start": sum(not row.get("started_at") for row in group),
            "after_session_end": (sum(parse_ts(row["created_at"]) > session_end for row in group)
                                  if session_end is not None else None),
            "fully_contained": _contained(group, start, session_end),
        })
    return summaries


def compute(scores: list[dict], sr_lookup=None, blocks=None, session_ended_at=None) -> dict:
    """sr_lookup(score) -> star rating (mod adjusted) or None."""
    rows = sorted(scores, key=lambda r: (parse_ts(r["created_at"]), r["score_id"]))
    timestamps = Counter(parse_ts(row["created_at"]) for row in rows)
    out: dict = {"plays": len(rows)}
    if rows:
        out["ordering_evidence"] = {
            "start_time_fallbacks": sum(row.get("timestamp_source") == "started_at" for row in rows),
            "unknown_timestamp_source": sum(row.get("timestamp_source") not in ("ended_at", "started_at") for row in rows),
            "tied_timestamp_groups": sum(count > 1 for count in timestamps.values()),
            "scores_in_tied_groups": sum(count for count in timestamps.values() if count > 1),
        }
    if blocks:
        session_end = parse_ts(session_ended_at) if session_ended_at else None
        summaries = []
        for i, marker in enumerate(blocks):
            start = parse_ts(marker["started_at"])
            end = parse_ts(blocks[i + 1]["started_at"]) if i + 1 < len(blocks) else None
            selected = [row for row in rows if row.get("timestamp_source") != "started_at"
                        and parse_ts(row["created_at"]) >= start
                        and (end is None or parse_ts(row["created_at"]) < end)]
            crossing = sum(parse_ts(row["started_at"]) < start for row in selected if row.get("started_at"))
            unknown_start = sum(not row.get("started_at") for row in selected)
            summaries.append({**marker, **_block(selected),
                              "crossing_start": crossing, "unknown_start": unknown_start,
                              "after_session_end": (sum(parse_ts(row["created_at"]) > session_end for row in selected)
                                                    if session_end is not None else None),
                              "fully_contained": _contained(selected, start, session_end),
                              "maps": _block_maps(selected, start, session_end)})
        out["blocks"] = summaries
        out["plays_before_first_block"] = sum(
            row.get("timestamp_source") != "started_at" and
            parse_ts(row["created_at"]) < parse_ts(blocks[0]["started_at"]) for row in rows
        )
        out["plays_with_unknown_end_time"] = sum(row.get("timestamp_source") == "started_at" for row in rows)
    if not rows:
        return out
    passed = [r for r in rows if r.get("passed") is True]
    seen = set()
    first_attempts, later_attempts = [], []
    for row in rows:
        target = later_attempts if row["beatmap_id"] in seen else first_attempts
        target.append(row)
        seen.add(row["beatmap_id"])
    out["first_recorded_attempts"] = _block(first_attempts)
    out["later_recorded_attempts"] = _block(later_attempts)
    out["passes"] = len(passed)
    out["hit_results"] = _hit_results(rows)
    out["pass_rate"] = _pass_rate(rows)
    out["pass_rate_samples"] = sum(type(row.get("passed")) is bool for row in rows)
    out["acc_passed"] = _acc(rows)
    out["acc_passed_samples"] = sum(_valid_accuracy(row) for row in passed)
    pps = [r["pp"] for r in rows if r.get("pp") is not None]
    out["best_pp"] = max(pps) if pps else None

    first, last = parse_ts(rows[0]["created_at"]), parse_ts(rows[-1]["created_at"])
    out["span_min"] = round((last - first).total_seconds() / 60, 1)

    def avg_sr(group):
        vals = [sr_lookup(r) for r in group] if sr_lookup else []
        vals = [v for v in vals if v is not None]
        return (round(mean(vals), 2) if vals else None, len(vals))

    out["avg_sr_passed"], out["avg_sr_passed_samples"] = avg_sr(passed)
    out["avg_sr_failed"], out["avg_sr_failed_samples"] = avg_sr([r for r in rows if r.get("passed") is False])

    half = len(rows) // 2
    out["first_half"] = _block(rows[:half or 1])
    out["second_half"] = _block(rows[half or 1:]) if len(rows) > 1 else _block([])

    # first vs last 20 minutes (only meaningful if the session is long enough)
    w = WINDOW_MIN * 60
    if (last - first).total_seconds() >= 2 * w:
        early = [r for r in rows if (parse_ts(r["created_at"]) - first).total_seconds() < w]
        late = [r for r in rows if (last - parse_ts(r["created_at"])).total_seconds() <= w]
        out["first_20min"], out["last_20min"] = _block(early), _block(late)
    else:
        out["first_20min"] = out["last_20min"] = None

    groups: dict[tuple, list[dict]] = defaultdict(list)
    consecutive_repeats = defaultdict(int)
    previous_key = None
    for r in rows:
        key = _condition_key(r)
        if key == previous_key:
            consecutive_repeats[key] += 1
        groups[key].append(r)
        previous_key = key
    maps = []
    for (bid, mods, settings), rs in groups.items():
        p = [x["pp"] for x in rs if x.get("pp") is not None]
        maps.append({
            "beatmap_id": bid, "title": rs[0]["title"], "version": rs[0]["version"],
            "mods": list(mods), "mod_settings": json.loads(settings),
            "plays": len(rs), "passes": sum(x.get("passed") is True for x in rs),
            "pass_rate_samples": sum(type(row.get("passed")) is bool for row in rs),
            "max_retry": max(x["retry_index"] or 0 for x in rs),
            "consecutive_recorded_repeats": consecutive_repeats[(bid, mods, settings)],
            "acc_passed": _acc(rs), "best_pp": max(p) if p else None,
            "acc_passed_samples": sum(row.get("passed") is True and _valid_accuracy(row) for row in rs),
            "hit_results": _hit_results(rs),
            "sr": sr_lookup(rs[0]) if sr_lookup else None,
        })
    out["maps"] = sorted(maps, key=lambda m: -m["plays"])
    return out


def _f(v, fmt="{}", none="-"):
    return none if v is None else fmt.format(v)


def render(session_id: str, s: dict) -> str:
    if not s.get("plays"):
        return f"{session_id}: no plays recorded"
    lines = [
        f"{session_id} | {s['plays']} plays, {s['passes']} passes ({_f(s['pass_rate'], '{}%')}; n={s['pass_rate_samples']}) | "
        f"span {s['span_min']} min | best pp {_f(s['best_pp'], '{:.0f}')}",
        f"acc (passes) {_f(s['acc_passed'], '{}%')} (n={s.get('acc_passed_samples', '?')}) | "
        f"avg SR passed {_f(s['avg_sr_passed'])} (n={s.get('avg_sr_passed_samples', '?')}) "
        f"vs failed {_f(s['avg_sr_failed'])} (n={s.get('avg_sr_failed_samples', '?')})",
    ]
    for label, key in (("1st half", "first_half"), ("2nd half", "second_half"),
                       ("first 20m", "first_20min"), ("last 20m", "last_20min")):
        b = s.get(key)
        if b:
            lines.append(f"{label:9}: {b['plays']:>3} plays, pass {_f(b['pass_rate'], '{}%')}, "
                         f"acc {_f(b['acc_passed'], '{}%')}")
    lines.append("")
    lines.append(f"{'map':38} {'mods':6} {'plays':>5} {'pass':>4} {'rept':>5} {'acc%':>6} {'pp':>5}")
    for m in s["maps"][:12]:
        name = f"{m['title']} [{m['version']}]"
        lines.append(f"{name[:38]:38} {''.join(m['mods'])[:6]:6} {m['plays']:>5} {m['passes']:>4} "
                     f"{m['consecutive_recorded_repeats']:>5} {_f(m['acc_passed']):>6} {_f(m['best_pp'], '{:.0f}'):>5}")
        if m.get("mod_settings"):
            lines.append(f"  settings: {json.dumps(m['mod_settings'], ensure_ascii=False, sort_keys=True)}")
    if len(s["maps"]) > 12:
        lines.append(f"... +{len(s['maps']) - 12} more maps (use --json)")
    return "\n".join(lines)
