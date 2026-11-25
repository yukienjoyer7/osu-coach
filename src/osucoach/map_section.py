"""Inspect explicitly selected hit-object onsets in a local osu!standard map."""
import hashlib
import math
import re
from bisect import bisect_left, bisect_right
from pathlib import Path

from .api import OsuError

MAX_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_OBJECTS = 1000


def _coordinate(value):
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1_000_000:
        raise ValueError
    return number


def score_evidence(section, saved):
    score = saved["score"]
    score_map = score.get("beatmap_id")
    declared_map = section.get("declared_beatmap_id")
    checksum = score.get("beatmap_checksum")
    checksum_valid = isinstance(checksum, str) and re.fullmatch(r"[0-9a-fA-F]{32}", checksum)
    speed_setting = None
    mods = score.get("mods")
    settings = score.get("mod_settings")
    if score.get("mods_known") is True and isinstance(mods, list) and isinstance(settings, dict):
        speed_mods = [mod for mod in mods if mod in ("DT", "NC", "HT", "DC")]
        if len(speed_mods) == 1 and not any(mod in ("WU", "WD", "AS") for mod in mods):
            values = settings.get(speed_mods[0])
            value = values.get("speed_change") if isinstance(values, dict) else None
            if type(value) in (int, float) and math.isfinite(value) and value > 0:
                speed_setting = value
    return {"source": saved["source"], "session_id": saved["session_id"], "score_id": score.get("score_id"),
            "score_retrieved_at": score.get("retrieved_at"), "scores_sync": saved["scores_sync"],
            "declared_beatmap_matches_saved_score": (declared_map == score_map
                if type(declared_map) is int and type(score_map) is int else None),
            "file_matches_fetch_time_checksum": (section["file_md5"] == checksum.lower()
                                                  if checksum_valid else None),
            "explicit_saved_speed_setting": speed_setting,
            "rate_matches_explicit_score_setting": (math.isclose(section["constant_rate"], speed_setting,
                                                                   rel_tol=0, abs_tol=1e-9)
                                                     if speed_setting is not None else None)}


def inspect(path, start_ms, end_ms, rate=1.0):
    if start_ms < 0 or end_ms <= start_ms:
        raise OsuError("section must have a non-negative start and a later end")
    if not math.isfinite(rate) or not 0.5 <= rate <= 2.0:
        raise OsuError("rate must be finite and between 0.5 and 2.0")
    path = Path(path).expanduser()
    if path.suffix.casefold() != ".osu":
        raise OsuError("map must be a local .osu file")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        raise OsuError("could not read .osu file") from None
    if len(raw) > MAX_BYTES:
        raise OsuError(".osu file exceeds 8 MiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise OsuError(".osu file must be UTF-8") from None
    section = None
    mode = 0
    declared_id = None
    objects = []
    saw_objects = False
    timing_points = []
    saw_timing = False
    invalid_timing_lines = 0
    try:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                saw_objects |= section == "HitObjects"
                saw_timing |= section == "TimingPoints"
                continue
            if section == "General" and line.startswith("Mode:"):
                mode = int(line.split(":", 1)[1])
            elif section == "Metadata" and line.startswith("BeatmapID:"):
                value = int(line.split(":", 1)[1])
                declared_id = value if value > 0 else None
            elif section == "TimingPoints":
                fields = line.split(",")
                try:
                    time = float(fields[0])
                    flag = fields[6].strip() if len(fields) >= 7 else "1"
                    if not math.isfinite(time) or flag not in ("0", "1"):
                        raise ValueError
                    if flag == "1":
                        length = float(fields[1])
                        bpm = 60000 / length if math.isfinite(length) and length > 0 else None
                        if bpm is not None and not math.isfinite(bpm * rate):
                            bpm = None
                        timing_points.append({"map_time_ms": time, "beat_length_ms": length if bpm is not None else None,
                                              "bpm": round(bpm, 3) if bpm is not None else None,
                                              "playback_bpm": round(bpm * rate, 3) if bpm is not None else None})
                except (ValueError, IndexError, OverflowError, ZeroDivisionError):
                    invalid_timing_lines += 1
            elif section == "HitObjects":
                fields = line.split(",")
                x, y = _coordinate(fields[0]), _coordinate(fields[1])
                time, flags = float(fields[2]), int(fields[3])
                kind = flags & (1 | 2 | 8 | 128)
                if (len(fields) < 5 or kind not in (1, 2, 8) or not 0 <= flags <= 255
                        or not math.isfinite(time) or not -(2**31) <= time <= 2**31 - 1):
                    raise ValueError
                objects.append({"map_time_ms": time, "type": {1: "circle", 2: "slider", 8: "spinner"}[kind],
                                "x": x, "y": y})
    except (ValueError, IndexError):
        raise OsuError(".osu file has malformed or unsupported hit objects") from None
    if mode != 0:
        raise OsuError("only osu!standard beatmaps are supported")
    if not saw_objects:
        raise OsuError(".osu file has no HitObjects section")
    unordered = any(a["map_time_ms"] > b["map_time_ms"] for a, b in zip(objects, objects[1:]))
    objects.sort(key=lambda obj: obj["map_time_ms"])
    timing_unordered = any(a["map_time_ms"] > b["map_time_ms"] for a, b in zip(timing_points, timing_points[1:]))
    timing_points.sort(key=lambda item: item["map_time_ms"])
    timing_times = [item["map_time_ms"] for item in timing_points]
    before = [item for item in timing_points if item["map_time_ms"] <= start_ms]
    initial = [item for item in before if item["map_time_ms"] == before[-1]["map_time_ms"]] if before else []
    changes = [item for item in timing_points if start_ms < item["map_time_ms"] < end_ms]
    selected = [obj for obj in objects if start_ms <= obj["map_time_ms"] < end_ms]
    output = []
    previous = None
    for obj in selected[:MAX_OUTPUT_OBJECTS]:
        gap = (obj["map_time_ms"] - previous["map_time_ms"]) if previous else None
        gap_beats = None
        if previous is not None and not invalid_timing_lines:
            start = previous["map_time_ms"]
            end = obj["map_time_ms"]
            index = bisect_right(timing_times, start) - 1
            if (index >= 0 and bisect_left(timing_times, timing_times[index]) == index
                    and (index + 1 == len(timing_times) or timing_times[index + 1] > end)):
                beat_length = timing_points[index]["beat_length_ms"]
                if beat_length is not None:
                    ratio = gap / beat_length
                    gap_beats = round(ratio, 4) if math.isfinite(ratio) else None
        distance = None
        if previous and previous["type"] == obj["type"] == "circle":
            distance = round(math.hypot(obj["x"] - previous["x"], obj["y"] - previous["y"]), 2)
        output.append({**obj, "playback_time_ms": round(obj["map_time_ms"] / rate, 2),
                       "previous_onset_gap_ms": round(gap / rate, 2) if gap is not None else None,
                       "previous_onset_gap_beats": gap_beats,
                       "previous_file_circle_position_distance_osu_px": distance})
        previous = obj
    return {"file_md5": hashlib.md5(raw).hexdigest(), "file_sha256": hashlib.sha256(raw).hexdigest(),
            "score_evidence": None,
            "declared_beatmap_id": declared_id, "section_map_time_ms": {"start": start_ms, "end_exclusive": end_ms},
            "constant_rate": rate, "input_was_unordered": unordered,
            "timing": {"section_present": saw_timing, "invalid_lines": invalid_timing_lines,
                       "red_points_unordered": timing_unordered,
                       "initial_candidates": initial[:MAX_OUTPUT_OBJECTS] if not invalid_timing_lines else None,
                       "initial_candidates_truncated": len(initial) > MAX_OUTPUT_OBJECTS,
                       "changes": changes[:MAX_OUTPUT_OBJECTS], "changes_truncated": len(changes) > MAX_OUTPUT_OBJECTS},
            "selected_object_count": len(selected), "returned_object_count": len(output),
            "truncated": len(selected) > len(output), "onsets": output,
            "limitations": [
                "Local file identity and declared beatmap ID are not verified against an API score or historical map version.",
                "Object times describe starts, not actual taps, replay judgements or slider ends.",
                "Timing entries are raw uninherited points, not verified client timing; simultaneous or malformed points can be ambiguous.",
                "Map BPM does not establish tapping subdivision, speed or player ability.",
                "Beat-relative gaps describe adjacent object starts, not necessarily taps or a recognized rhythm pattern.",
                "Times are raw file timestamps; client-specific offsets or conversion are not applied.",
                "Distances use raw file positions; client clamping, rounding, stacking, cursor path and mod effects are not simulated.",
                "The constant rate is supplied by the user; variable speed and other mods are not simulated.",
                "No pattern classification, difficulty rating, player weakness or cause of misses is established.",
            ]}
