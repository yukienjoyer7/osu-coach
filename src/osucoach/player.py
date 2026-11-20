"""Read the session setup from the player's Markdown profile."""
import re
from pathlib import Path

from .api import OsuError

SETUP_FIELDS = (
    "input", "tablet", "area_mm", "area_center_mm", "area_offset_mm",
    "area_rotation_deg", "driver", "keypad", "fps", "client",
)

NUMBER = r"-?\d+(?:\.\d+)?"


def read_setup(path: Path) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise OsuError(f"could not read player setup from {path}: {exc}") from exc

    fields = {}
    for line in lines:
        if line.startswith("- "):
            label, separator, value = line[2:].partition(":")
            value = value.strip()
            if separator:
                fields[label.strip().casefold()] = (
                    value if value and not value.startswith("<") else None
                )

    # Older profiles list the tablet, keypad and FPS together under "setup".
    device, _, peripherals = (fields.get("setup") or "").partition(",")
    keypad, _, fps = peripherals.partition(",")
    device = device.split(" (", 1)[0].strip()
    input_type = fields.get("input")
    if input_type is None and device:
        input_type = "mouse" if "mouse" in device.casefold() else "tablet"
    if input_type:
        input_type = input_type.casefold()

    area, _, inline_driver = (fields.get("tablet area") or "").partition(", driver:")
    size = re.match(rf"({NUMBER})\s*[x×]\s*({NUMBER})\s*mm\b", area, re.IGNORECASE)
    center = re.search(rf"\bcenter\s*\(\s*({NUMBER})\s*,\s*({NUMBER})\s*\)", area, re.IGNORECASE)
    rotation = re.search(rf"\brotation\s*({NUMBER})\s*°", area, re.IGNORECASE)
    fps_match = re.fullmatch(r"~?\s*(\d+)(?:\s*fps)?", fields.get("fps", fps.strip()) or "", re.IGNORECASE)
    client = re.fullmatch(r"(lazer|stable)(?:\s+\([^)]*\))?", fields.get("client") or "", re.IGNORECASE)

    return {
        "input": input_type,
        "tablet": fields.get("tablet", device or None) if input_type == "tablet" else None,
        "area_mm": [float(value) for value in size.groups()] if size else None,
        "area_center_mm": [float(value) for value in center.groups()] if center else None,
        "area_offset_mm": None,
        "area_rotation_deg": float(rotation[1]) if rotation else None,
        "driver": fields.get("driver", inline_driver.strip() or None),
        "keypad": fields.get("keypad", keypad.strip() or None),
        "fps": int(fps_match[1]) if fps_match else None,
        "client": client[1].casefold() if client else None,
    }
