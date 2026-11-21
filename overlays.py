"""Pure builders for the top 8 / player path overlay JSON."""
import json
import os
import tempfile
from pathlib import Path

import startgg
from startgg import _slot

ROUND_KEYS = [
    "winnersSemis", "winnersFinal", "grandFinal", "grandFinalReset",
    "losersTop8", "losersQuarters", "losersSemis", "losersFinal",
]

_EXPECTED = {
    "winnersSemis": 2, "winnersFinal": 1, "grandFinal": 1,
    "losersTop8": 2, "losersQuarters": 2, "losersSemis": 1, "losersFinal": 1,
}

_RESET_TEXT = "Grand Final Reset"


def _empty_slot():
    return {"name": None, "team": "", "score": None, "winner": False}


def _empty_match():
    return {"setId": None, "state": None, "p1": _empty_slot(), "p2": _empty_slot()}


def _same_id(a, b):
    """start.gg sometimes returns ids as strings; compare as strings."""
    return a is not None and b is not None and str(a) == str(b)


def _score(slot_dict, completed):
    if not completed:
        return None
    value = (((slot_dict or {}).get("standing") or {}).get("stats") or {}).get("score") or {}
    value = value.get("value")
    if value is None:
        return None
    if value == -1:
        return "DQ"
    return int(value)


def _match(set_node):
    slots = set_node.get("slots") or []
    completed = set_node.get("state") == 3
    winner_id = set_node.get("winnerId")
    out = {"setId": set_node.get("id"), "state": set_node.get("state")}
    for i, key in enumerate(("p1", "p2")):
        eid, name, team = _slot(slots, i)
        out[key] = {
            "name": name,
            "team": team,
            "score": _score(slots[i], completed) if i < len(slots) else None,
            "winner": _same_id(eid, winner_id),
        }
    return out


def _has_both(set_node):
    slots = set_node.get("slots") or []
    return _slot(slots, 0)[0] is not None and _slot(slots, 1)[0] is not None


def _sorted_matches(sets, round_no, positive):
    if round_no is None or (round_no > 0) != positive:
        return []
    found = [s for s in sets if s.get("round") == round_no]
    found.sort(key=lambda s: (len(s.get("identifier") or ""), s.get("identifier") or ""))
    return [_match(s) for s in found]


def build_top8(sets, tournament_name, event_name, now):
    resets = [s for s in sets if s.get("fullRoundText") == _RESET_TEXT]
    regular = [s for s in sets if s.get("fullRoundText") != _RESET_TEXT]
    pos = [s["round"] for s in regular if (s.get("round") or 0) > 0]
    neg = [s["round"] for s in regular if (s.get("round") or 0) < 0]
    w = max(pos) if pos else None
    l = min(neg) if neg else None

    plan = {
        "winnersSemis": (None if w is None else w - 2, True),
        "winnersFinal": (None if w is None else w - 1, True),
        "grandFinal": (w, True),
        "losersTop8": (None if l is None else l + 3, False),
        "losersQuarters": (None if l is None else l + 2, False),
        "losersSemis": (None if l is None else l + 1, False),
        "losersFinal": (l, False),
    }
    rounds = {}
    for key in ROUND_KEYS:
        if key == "grandFinalReset":
            played = [s for s in resets if _has_both(s)]
            rounds[key] = [_match(s) for s in played[:1]]
            continue
        rnd, positive = plan[key]
        matches = _sorted_matches(regular, rnd, positive)[:_EXPECTED[key]]
        while len(matches) < _EXPECTED[key]:
            matches.append(_empty_match())
        rounds[key] = matches
    return {
        "updatedAt": now,
        "tournamentName": tournament_name,
        "eventName": event_name,
        "rounds": rounds,
    }


def _path_score(mine, theirs):
    if mine == "DQ" or theirs == "DQ":
        return "DQ"
    if mine is None and theirs is None:
        return ""
    return f"{mine or 0}-{theirs or 0}"


def build_player_path(sets, entrant_id, active_set, event_name, now):
    active_slots = active_set.get("slots") or []
    player = {"name": None, "team": ""}
    for i in range(2):
        eid, name, team = _slot(active_slots, i)
        if _same_id(eid, entrant_id):
            player = {"name": name, "team": team}
            break

    rows = []
    for s in sets:
        if s.get("state") != 3 or _same_id(s.get("id"), active_set.get("id")):
            continue
        slots = s.get("slots") or []
        ids = [_slot(slots, 0)[0], _slot(slots, 1)[0]]
        if ids[0] is None or ids[1] is None:
            continue
        if _same_id(ids[0], entrant_id):
            mi = 0
        elif _same_id(ids[1], entrant_id):
            mi = 1
        else:
            continue
        _, opp_name, opp_team = _slot(slots, 1 - mi)
        score = _path_score(_score(slots[mi], True), _score(slots[1 - mi], True))
        rows.append((s.get("completedAt"), {
            "round": s.get("fullRoundText"),
            "opponent": {"name": opp_name, "team": opp_team},
            "score": score,
            "won": _same_id(s.get("winnerId"), entrant_id),
        }))
    rows.sort(key=lambda r: (r[0] is None, r[0] or 0))
    return {
        "updatedAt": now,
        "eventName": event_name,
        "player": player,
        "currentRound": active_set.get("fullRoundText"),
        "sets": [r[1] for r in rows],
    }


DEFAULT_STATE = {"setId": None, "p1Id": None, "p2Id": None, "pathSide": "p1"}


def load_state(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return dict(DEFAULT_STATE)
    if not isinstance(data, dict):
        return dict(DEFAULT_STATE)
    return {k: data.get(k, v) for k, v in DEFAULT_STATE.items()}


def write_json_atomic(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(path, state):
    write_json_atomic(path, state)


def merge_active_state(prev, body):
    side = body.get("pathSide")
    side = side if side in ("p1", "p2") else "p1"
    if not body.get("setId"):
        out = dict(prev)
        out["pathSide"] = side
        return out
    return {"setId": body["setId"], "p1Id": body.get("p1Id"),
            "p2Id": body.get("p2Id"), "pathSide": side}


def refresh(state, token, sc_dir, now, context_cache):
    set_id = state.get("setId")
    if not set_id:
        return False
    ctx = context_cache.get(set_id)
    if ctx is None:
        ctx = startgg.fetch_set_context(set_id, token)
        context_cache.clear()
        context_cache[set_id] = ctx
    event = ctx.get("event") or {}
    tournament_name = (event.get("tournament") or {}).get("name")
    event_name = event.get("name")
    pg_id = (ctx.get("phaseGroup") or {}).get("id")
    entrant_id = state.get("p2Id" if state.get("pathSide") == "p2" else "p1Id")

    group_sets = startgg.fetch_phase_group_sets(pg_id, token)
    entrant_sets = startgg.fetch_entrant_event_sets(event.get("id"), entrant_id, token)
    top8 = build_top8(group_sets, tournament_name, event_name, now)
    path = build_player_path(entrant_sets, entrant_id, ctx, event_name, now)
    sc_dir = Path(sc_dir)
    write_json_atomic(sc_dir / "top8.json", top8)
    write_json_atomic(sc_dir / "playerpath.json", path)
    return True
