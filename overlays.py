"""Pure builders for the top 8 / player path overlay JSON."""
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
    pos = [s["round"] for s in regular if s.get("round", 0) > 0]
    neg = [s["round"] for s in regular if s.get("round", 0) < 0]
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
