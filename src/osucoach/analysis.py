"""Store external analysis as untrusted evidence, without interpreting its claims."""
import hashlib
import json
import math
from pathlib import Path
import re

from . import paths
from .api import OsuError
from .store import now_local, read_json, write_json

MAX_BYTES = 2 * 1024 * 1024


def _reject_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def import_report(session_id, path, source, version, beatmap_id, score_id=None):
    source = source.strip()
    if not source:
        raise OsuError("analysis source cannot be empty")
    path = Path(path).expanduser().resolve()
    if path.suffix.casefold() != ".json":
        raise OsuError("analysis report must be a JSON file")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        raise OsuError("could not read analysis report") from None
    if len(raw) > MAX_BYTES:
        raise OsuError("analysis report exceeds 2 MiB")
    try:
        report = json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, UnicodeDecodeError):
        raise OsuError("analysis report must contain valid JSON") from None
    if not isinstance(report, dict):
        raise OsuError("analysis report must be a JSON object")
    digest = hashlib.sha256(raw).hexdigest()
    identity = json.dumps([digest, source, version, beatmap_id, score_id], separators=(",", ":"))
    report_id = hashlib.sha256(identity.encode()).hexdigest()
    destination = paths.session_dir(session_id) / "analyses" / f"{report_id}.json"
    if destination.exists():
        return metadata(read_json(destination), session_id)
    entry = {"id": report_id, "imported_at": now_local().isoformat(timespec="seconds"),
             "source": source, "version": version, "beatmap_id": beatmap_id, "score_id": score_id,
             "report_sha256": digest, "report": report}
    write_json(destination, entry)
    return metadata(entry, session_id)


def metadata(entry, session_id):
    rows = read_json(paths.session_dir(session_id) / "scores.json")
    score_id = entry.get("score_id")
    match = None
    checksum_match = None
    rate_match = None
    reported_md5 = None
    reported_rate = None
    reported_speed_mod = None
    reported_flags = None
    report = entry.get("report")
    if entry.get("source", "").casefold() == "osu-critique" and isinstance(report, dict):
        candidate = report.get("beatmap_md5")
        if isinstance(candidate, str) and re.fullmatch(r"[0-9a-fA-F]{32}", candidate):
            reported_md5 = candidate.lower()
        trust = report.get("trust") if isinstance(report.get("trust"), dict) else {}
        def flag(value):
            return value if type(value) is bool else None
        reported_flags = {"trustworthy": flag(trust.get("trustworthy")),
                          "map_version_mismatch": flag(report.get("map_version_mismatch")),
                          "failed_play": flag(report.get("failed_play")),
                          "scale_overridden": flag(trust.get("scale_overridden"))}
        score_meta = report.get("score_meta")
        if isinstance(score_meta, dict):
            rate = score_meta.get("speed_change")
            mods = score_meta.get("mods")
            if type(rate) in (int, float) and math.isfinite(rate) and rate > 0 and isinstance(mods, list):
                speed_mods = [mod.get("acronym") for mod in mods if isinstance(mod, dict)
                              and mod.get("acronym") in ("DT", "NC", "HT", "DC")]
                if len(speed_mods) == 1:
                    reported_rate, reported_speed_mod = rate, speed_mods[0]
    if rows is not None and score_id is not None:
        matching = [row for row in rows if row.get("score_id") == score_id]
        if matching and type(matching[0].get("beatmap_id")) is int:
            match = matching[0]["beatmap_id"] == entry["beatmap_id"]
            checksum = matching[0].get("beatmap_checksum")
            if match and reported_md5 and isinstance(checksum, str) and re.fullmatch(r"[0-9a-fA-F]{32}", checksum):
                checksum_match = checksum.lower() == reported_md5
            saved_mods = matching[0].get("mods")
            settings = matching[0].get("mod_settings")
            if match and reported_speed_mod and isinstance(saved_mods, list) and isinstance(settings, dict):
                speed_mods = [mod for mod in saved_mods if mod in ("DT", "NC", "HT", "DC")]
                aliases = {"NC": "DT", "DC": "HT"}
                if len(speed_mods) == 1 and aliases.get(speed_mods[0], speed_mods[0]) == aliases.get(reported_speed_mod, reported_speed_mod):
                    saved_settings = settings.get(speed_mods[0])
                    saved_rate = saved_settings.get("speed_change") if isinstance(saved_settings, dict) else None
                    if type(saved_rate) in (int, float) and math.isfinite(saved_rate) and saved_rate > 0:
                        rate_match = saved_rate == reported_rate
    return ({key: value for key, value in entry.items() if key != "report"} |
            {"saved_score_map_match": match, "reported_beatmap_md5": reported_md5,
             "saved_score_checksum_match": checksum_match, "analyzer_reported_flags": reported_flags,
             "reported_speed_change": reported_rate, "reported_rate_matches_saved_setting": rate_match})


def list_reports(session_id):
    directory = paths.session_dir(session_id) / "analyses"
    return [metadata(read_json(path), session_id) for path in sorted(directory.glob("*.json"))]


def show_report(session_id, report_id):
    if not re.fullmatch(r"[0-9a-f]{64}", report_id):
        raise OsuError("invalid analysis ID")
    entry = read_json(paths.session_dir(session_id) / "analyses" / f"{report_id}.json")
    if entry is None:
        raise OsuError("analysis report not found")
    return {**metadata(entry, session_id), "report": entry["report"],
            "interpretation": "Untrusted external report; claims and score identity are not independently verified."}
