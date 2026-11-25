"""Inspect explicitly selected hit-object onsets in a local osu!standard map."""
import hashlib
import math
from pathlib import Path

from .api import OsuError

MAX_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_OBJECTS = 1000


def _coordinate(value):
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1_000_000:
        raise ValueError
    return number


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
    try:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                saw_objects |= section == "HitObjects"
                continue
            if section == "General" and line.startswith("Mode:"):
                mode = int(line.split(":", 1)[1])
            elif section == "Metadata" and line.startswith("BeatmapID:"):
                value = int(line.split(":", 1)[1])
                declared_id = value if value > 0 else None
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
    selected = [obj for obj in objects if start_ms <= obj["map_time_ms"] < end_ms]
    output = []
    previous = None
    for obj in selected[:MAX_OUTPUT_OBJECTS]:
        gap = (obj["map_time_ms"] - previous["map_time_ms"]) if previous else None
        distance = None
        if previous and previous["type"] == obj["type"] == "circle":
            distance = round(math.hypot(obj["x"] - previous["x"], obj["y"] - previous["y"]), 2)
        output.append({**obj, "playback_time_ms": round(obj["map_time_ms"] / rate, 2),
                       "previous_onset_gap_ms": round(gap / rate, 2) if gap is not None else None,
                       "previous_file_circle_position_distance_osu_px": distance})
        previous = obj
    return {"file_md5": hashlib.md5(raw).hexdigest(), "file_sha256": hashlib.sha256(raw).hexdigest(),
            "declared_beatmap_id": declared_id, "section_map_time_ms": {"start": start_ms, "end_exclusive": end_ms},
            "constant_rate": rate, "input_was_unordered": unordered,
            "selected_object_count": len(selected), "returned_object_count": len(output),
            "truncated": len(selected) > len(output), "onsets": output,
            "limitations": [
                "Local file identity and declared beatmap ID are not verified against an API score or historical map version.",
                "Times describe object starts, not actual taps, replay judgements, timing points or slider ends.",
                "Times are raw file timestamps; client-specific offsets or conversion are not applied.",
                "Distances use raw file positions; client clamping, rounding, stacking, cursor path and mod effects are not simulated.",
                "The constant rate is supplied by the user; variable speed and other mods are not simulated.",
                "No pattern classification, difficulty rating, player weakness or cause of misses is established.",
            ]}
