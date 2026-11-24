"""Workspace account binding for newly fetched player data."""
from . import paths
from .api import OsuError
from .store import now_local, read_json, write_json


def binding():
    return read_json(paths.DATA / "account.json")


def check(user_id):
    user_id = str(user_id or "").strip()
    if not user_id:
        raise OsuError("OSU_USER_ID is missing; configure the player account")
    if not user_id.isascii() or not user_id.isdecimal() or int(user_id) < 1:
        raise OsuError("OSU_USER_ID must be a positive numeric osu! user ID")
    saved = binding()
    if saved and saved.get("user_id") != user_id:
        raise OsuError("workspace is bound to another osu! user; use a separate OSUCOACH_HOME")
    return user_id


def bind(user_id):
    user_id = check(user_id)
    saved = binding()
    if saved is not None:
        return saved
    legacy = (paths.BEST.exists() or
              (paths.PROFILE_DIR.exists() and any(paths.PROFILE_DIR.glob("*.json"))) or
              (paths.SESSIONS.exists() and any(paths.SESSIONS.glob("*/scores.json"))))
    saved = {"user_id": user_id, "bound_at": now_local().isoformat(timespec="seconds"),
             "legacy_data_present": bool(legacy)}
    write_json(paths.DATA / "account.json", saved)
    return saved


def check_response(user_id, rows):
    """Reject explicit numeric identity mismatches; missing IDs stay unknown."""
    if not user_id.isdecimal():
        return
    expected = int(user_id)
    if any(row.get("user_id") is not None and row["user_id"] != expected for row in rows):
        raise OsuError("score data belongs to another osu! user; use a separate workspace")
