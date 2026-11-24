"""Mod handling: parsing, normalising for difficulty, and mod-adjusted map stats.

A mod is a dict {"acronym": "DT", "settings": {...}} (settings optional).
"""
import hashlib
import json
import re

from .api import OsuError

SPEED = {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}
ALIAS = {"NC": "DT", "DC": "HT"}  # same star rating / stats as DT / HT


def parse_mods(arg: str | None) -> list[dict]:
    """Parse two-character acronyms without silently discarding malformed input."""
    if arg is None or not arg.strip():
        return []
    text = arg.strip().upper()
    if "," in text:
        parts = [part.strip() for part in text.split(",")]
        valid = all(re.fullmatch(r"[A-Z0-9]{2}", part) for part in parts)
    else:
        valid = bool(re.fullmatch(r"(?:[A-Z0-9]{2})+", text))
        parts = [text[i:i + 2] for i in range(0, len(text), 2)]
    if not valid or ("NM" in parts and len(parts) != 1) or len(set(parts)) != len(parts):
        raise OsuError("--mods must contain distinct two-character acronyms, e.g. HD,DT or HDDT; NM must stand alone")
    selected = set(parts)
    if (len(selected & {"DT", "NC", "HT", "DC", "WU", "WD", "AS"}) > 1 or
            len(selected & {"EZ", "HR", "DA"}) > 1):
        raise OsuError("--mods contains incompatible speed or difficulty adjustment mods")
    return [{"acronym": part} for part in parts if part != "NM"]


def from_score(acronyms: list[str], settings: dict | None = None) -> list[dict]:
    settings = settings or {}
    return [({"acronym": a, "settings": settings[a]} if a in settings else {"acronym": a})
            for a in acronyms]


def normalize(mods: list[dict]) -> list[dict]:
    """Preserve mods and settings for API difficulty calculations and cache identity."""
    out = []
    for m in mods:
        a = ALIAS.get(m["acronym"], m["acronym"])
        settings = dict(m.get("settings") or {})
        if settings.get("speed_change") == SPEED.get(m["acronym"]) and "speed_change" in settings:
            settings.pop("speed_change")
        out.append({"acronym": a, **({"settings": settings} if settings else {})})
    return sorted(out, key=lambda m: (m["acronym"], json.dumps(m.get("settings", {}), sort_keys=True)))


def key(mods: list[dict]) -> str:
    """Stable cache key for the complete normalized mod combination and settings."""
    normalized = normalize(mods)
    if not normalized:
        return "NM"
    # A versioned digest avoids collisions between settings and old incomplete keys.
    def canonical(value):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical(v) for v in value]
        return value
    encoded = json.dumps(canonical(normalized), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    version = "v4" if any(m["acronym"] in {"WU", "WD", "AS"} for m in normalized) else "v3"
    return version + "-" + hashlib.sha256(encoded.encode()).hexdigest()[:24]


def rate(mods: list[dict]) -> float:
    r = 1.0
    for m in mods:
        a = m["acronym"]
        if a in SPEED:
            r = (m.get("settings") or {}).get("speed_change", SPEED[a])
    return r


def _ar_to_ms(ar: float) -> float:
    return 1200 - 150 * (ar - 5) if ar > 5 else 1800 - 120 * ar


def _ms_to_ar(ms: float) -> float:
    return (1800 - ms) / 120 if ms > 1200 else 5 + (1200 - ms) / 150


def adjust(cs: float, ar: float, od: float, bpm: float | None, length_s: float,
           mods: list[dict]) -> dict:
    """Mod-adjusted CS/AR/OD/BPM/length for osu!standard."""
    acr = {m["acronym"]: m.get("settings") or {} for m in mods}
    if "EZ" in acr:
        cs, ar, od = cs * 0.5, ar * 0.5, od * 0.5
    if "HR" in acr:
        cs, ar, od = min(cs * 1.3, 10), min(ar * 1.4, 10), min(od * 1.4, 10)
    if "DA" in acr:
        s = acr["DA"]
        cs = s["circle_size"] if s.get("circle_size") is not None else cs
        ar = s["approach_rate"] if s.get("approach_rate") is not None else ar
        od = s["overall_difficulty"] if s.get("overall_difficulty") is not None else od
    if {"WU", "WD", "AS"} & acr.keys():
        return {"cs": round(cs, 2), "ar": None, "od": None, "bpm": None, "length_s": None}
    r = rate(mods)
    if r != 1.0:
        ar = _ms_to_ar(_ar_to_ms(ar) / r)
        od = (80 - (80 - 6 * od) / r) / 6
    return {"cs": round(cs, 2), "ar": round(ar, 2), "od": round(od, 2),
            "bpm": round(bpm * r, 2) if bpm is not None else None, "length_s": round(length_s / r)}
