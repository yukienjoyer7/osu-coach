"""Mod handling: parsing, normalising for difficulty, and mod-adjusted map stats.

A mod is a dict {"acronym": "DT", "settings": {...}} (settings optional).
"""
import re

SPEED = {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}
ALIAS = {"NC": "DT", "DC": "HT"}  # same star rating / stats as DT / HT
DIFF_AFFECTING = {"EZ", "HR", "DT", "HT", "DA"}
KEEP_SETTINGS = {"speed_change", "circle_size", "approach_rate", "overall_difficulty"}


def parse_mods(arg: str | None) -> list[dict]:
    """'HD,DT' or 'HDDT' -> [{'acronym': 'HD'}, {'acronym': 'DT'}]."""
    if not arg:
        return []
    arg = arg.strip().upper()
    parts = arg.split(",") if "," in arg else re.findall(r"..", arg)
    return [{"acronym": p.strip()} for p in parts if p.strip() and p.strip() != "NM"]


def from_score(acronyms: list[str], settings: dict | None = None) -> list[dict]:
    settings = settings or {}
    return [({"acronym": a, "settings": settings[a]} if a in settings else {"acronym": a})
            for a in acronyms]


def normalize(mods: list[dict]) -> list[dict]:
    """Keep only difficulty-affecting mods, map NC->DT and DC->HT, sort by acronym."""
    out = []
    for m in mods:
        a = ALIAS.get(m["acronym"], m["acronym"])
        if a not in DIFF_AFFECTING:
            continue
        s = {k: v for k, v in (m.get("settings") or {}).items() if k in KEEP_SETTINGS}
        if "speed_change" in s and s["speed_change"] == SPEED.get(m["acronym"]):
            s.pop("speed_change")  # default rate, not a custom one
        out.append({"acronym": a, **({"settings": s} if s else {})})
    return sorted(out, key=lambda m: m["acronym"])


def key(mods: list[dict]) -> str:
    """Filename-safe cache key for a normalised mod list: 'NM', 'DT', 'DT1.3', 'EZDT'."""
    n = normalize(mods)
    if not n:
        return "NM"
    parts = []
    for m in n:
        extra = "".join(f"-{k[:2]}{v}" for k, v in sorted((m.get("settings") or {}).items()))
        parts.append(m["acronym"] + extra)
    return "".join(parts)


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


def adjust(cs: float, ar: float, od: float, bpm: float, length_s: float,
           mods: list[dict]) -> dict:
    """Mod-adjusted CS/AR/OD/BPM/length for osu!standard."""
    acr = {m["acronym"]: m.get("settings") or {} for m in mods}
    if "EZ" in acr:
        cs, ar, od = cs * 0.5, ar * 0.5, od * 0.5
    if "HR" in acr:
        cs, ar, od = min(cs * 1.3, 10), min(ar * 1.4, 10), min(od * 1.4, 10)
    if "DA" in acr:
        s = acr["DA"]
        cs = s.get("circle_size", cs)
        ar = s.get("approach_rate", ar)
        od = s.get("overall_difficulty", od)
    r = rate(mods)
    if r != 1.0:
        ar = _ms_to_ar(_ar_to_ms(ar) / r)
        od = (80 - (80 - 6 * od) / r) / 6
    return {"cs": round(cs, 2), "ar": round(ar, 2), "od": round(od, 2),
            "bpm": round(bpm * r, 2), "length_s": round(length_s / r)}
