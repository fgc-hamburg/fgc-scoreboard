import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import overlays
from overlays import ROUND_KEYS, build_top8, build_player_path


def _slot(p):
    if p is None:
        return {"entrant": None, "standing": None}
    eid, tag, prefix, score = p
    return {
        "entrant": {
            "id": eid,
            "name": f"{prefix} | {tag}" if prefix else tag,
            "participants": [{"prefix": prefix, "gamerTag": tag}],
        },
        "standing": {"stats": {"score": {"value": score}}},
    }


def mkset(id, round, text, ident, p1=None, p2=None, winner=None, state=3,
          completed_at=None):
    return {
        "id": id,
        "round": round,
        "fullRoundText": text,
        "identifier": ident,
        "state": state,
        "winnerId": winner,
        "completedAt": completed_at,
        "slots": [_slot(p1), _slot(p2)],
    }


def full_bracket():
    sets = []
    n = [0]

    def add(rnd, text, count):
        for i in range(count):
            n[0] += 1
            sets.append(mkset(n[0], rnd, text, chr(ord("A") + n[0] % 26) + str(i),
                              (n[0] * 10, f"a{n[0]}", "", 2),
                              (n[0] * 10 + 1, f"b{n[0]}", "", 0), winner=n[0] * 10))
    add(1, "Winners Round 1", 4)
    add(2, "Winners Quarter-Final", 4)
    add(3, "Winners Semi-Final", 2)
    add(4, "Winners Final", 1)
    add(5, "Grand Final", 1)
    for r, c in [(-1, 4), (-2, 4), (-3, 2), (-4, 2), (-5, 1), (-6, 1)]:
        add(r, f"Losers {r}", c)
    return sets


class Top8Tests(unittest.TestCase):
    def test_top8_classifies_full_bracket(self):
        sets = full_bracket()
        # shuffle identifiers: make idents out of order within round 3
        r3 = [s for s in sets if s["round"] == 3]
        r3[0]["identifier"], r3[1]["identifier"] = "BB", "AA"
        out = build_top8(sets, "T", "E", 1)
        ids = lambda rnd: [s["id"] for s in sets if s["round"] == rnd]
        got = lambda k: [m["setId"] for m in out["rounds"][k]]
        self.assertEqual(got("winnersSemis"), [r3[1]["id"], r3[0]["id"]])
        self.assertEqual(got("winnersFinal"), ids(4))
        self.assertEqual(got("grandFinal"), ids(5))
        self.assertEqual(got("losersTop8"), ids(-3))
        self.assertEqual(got("losersQuarters"), ids(-4))
        self.assertEqual(got("losersSemis"), ids(-5))
        self.assertEqual(got("losersFinal"), ids(-6))
        all_ids = {m["setId"] for k in ROUND_KEYS for m in out["rounds"][k]}
        for s in sets:
            if s["round"] in (1, 2, -1, -2):
                self.assertNotIn(s["id"], all_ids)

    def test_top8_slot_fields(self):
        s = mkset(1, 1, "Grand Final", "A", (10, "Daigo", "EG", 2),
                  (11, "Tokido", "", 1), winner=10)
        m = build_top8([s], "T", "E", 1)["rounds"]["grandFinal"][0]
        self.assertEqual(m["p1"], {"name": "Daigo", "team": "EG", "score": 2, "winner": True})
        self.assertIs(m["p2"]["winner"], False)

    def test_top8_winner_string_id(self):
        s = mkset(1, 1, "Grand Final", "A", (10, "Daigo", "EG", 2),
                  (11, "Tokido", "", 1), winner="10")
        m = build_top8([s], "T", "E", 1)["rounds"]["grandFinal"][0]
        self.assertIs(m["p1"]["winner"], True)

    def test_top8_unreported_scores_none(self):
        s = mkset(1, 1, "Grand Final", "A", (10, "a", "", 2), (11, "b", "", 1), state=1)
        m = build_top8([s], "T", "E", 1)["rounds"]["grandFinal"][0]
        self.assertIsNone(m["p1"]["score"])
        self.assertIsNone(m["p2"]["score"])

    def test_top8_dq_score(self):
        s = mkset(1, 1, "Grand Final", "A", (10, "a", "", 0), (11, "b", "", -1), winner=10)
        m = build_top8([s], "T", "E", 1)["rounds"]["grandFinal"][0]
        self.assertEqual(m["p2"]["score"], "DQ")

    def test_top8_reset_detected(self):
        sets = full_bracket()
        sets.append(mkset(999, 6, "Grand Final Reset", "ZZ", (1, "a", "", 0), (2, "b", "", 0)))
        out = build_top8(sets, "T", "E", 1)
        self.assertEqual(len(out["rounds"]["grandFinalReset"]), 1)
        gf5 = [s["id"] for s in sets if s["round"] == 5]
        self.assertEqual([m["setId"] for m in out["rounds"]["grandFinal"]], gf5)

    def test_top8_unplayed_reset_hidden(self):
        sets = full_bracket()
        sets.append(mkset(999, 6, "Grand Final Reset", "ZZ", None, None, state=1))
        out = build_top8(sets, "T", "E", 1)
        self.assertEqual(out["rounds"]["grandFinalReset"], [])

    def test_top8_small_bracket_pads_missing_rounds(self):
        sets = [
            mkset(1, 1, "Winners Semi", "A", (1, "a", "", 2), (2, "b", "", 0), winner=1),
            mkset(2, 2, "Winners Final", "B", (1, "a", "", 2), (3, "c", "", 0), winner=1),
            mkset(3, 3, "Grand Final", "C", (1, "a", "", 2), (4, "d", "", 0), winner=1),
            mkset(4, -1, "Losers 1", "D", (2, "b", "", 2), (5, "e", "", 0), winner=2),
            mkset(5, -2, "Losers Final", "E", (2, "b", "", 2), (3, "c", "", 0), winner=2),
        ]
        out = build_top8(sets, "T", "E", 1)
        for k in ROUND_KEYS:
            self.assertIn(k, out["rounds"])
        self.assertEqual(len(out["rounds"]["winnersSemis"]), 2)
        self.assertEqual(len(out["rounds"]["losersTop8"]), 2)
        self.assertIsNone(out["rounds"]["losersTop8"][0]["setId"])

    def test_top8_no_losers_pads(self):
        out = build_top8([mkset(1, 1, "Grand Final", "A")], "T", "E", 1)
        self.assertEqual(len(out["rounds"]["losersFinal"]), 1)
        self.assertEqual(len(out["rounds"]["losersQuarters"]), 2)

    def test_top8_header_fields(self):
        out = build_top8([], "Tourney", "Event", 12345)
        self.assertEqual(out["tournamentName"], "Tourney")
        self.assertEqual(out["eventName"], "Event")
        self.assertEqual(out["updatedAt"], 12345)


class PlayerPathTests(unittest.TestCase):
    ME = (7, "Me", "HH")

    def _mine(self, score):
        return (7, "Me", "HH", score)

    def _active(self, my_slot=0):
        mine, opp = self._mine(None), (9, "Next", "", None)
        a, b = (mine, opp) if my_slot == 0 else (opp, mine)
        return mkset(100, 5, "Winners Final", "Z", a, b, state=2)

    def test_path_orders_chronologically_and_excludes_active(self):
        sets = [
            mkset(1, 1, "Winners Round 1", "A", self._mine(2), (11, "x", "", 0), winner=7, completed_at=300),
            mkset(2, 2, "Winners Round 2", "B", self._mine(2), (12, "y", "", 0), winner=7, completed_at=100),
            mkset(3, 3, "Winners Round 3", "C", self._mine(2), (13, "z", "", 0), winner=7, completed_at=200),
            self._active(),
        ]
        out = build_player_path(sets, 7, self._active(), "E", 1)
        self.assertEqual([s["round"] for s in out["sets"]],
                         ["Winners Round 2", "Winners Round 3", "Winners Round 1"])
        self.assertEqual(len(out["sets"]), 3)

    def test_path_score_from_player_perspective(self):
        s = mkset(1, 1, "R1", "A", (11, "Opp", "OT", 1), self._mine(2), winner=7, completed_at=1)
        out = build_player_path([s], 7, self._active(), "E", 1)
        self.assertEqual(out["sets"][0], {"round": "R1", "opponent": {"name": "Opp", "team": "OT"},
                                          "score": "2-1", "won": True})

    def test_path_includes_losses(self):
        s = mkset(1, 1, "R1", "A", self._mine(0), (11, "Opp", "", 2), winner=11, completed_at=1)
        out = build_player_path([s], 7, self._active(), "E", 1)
        self.assertEqual(out["sets"][0]["score"], "0-2")
        self.assertIs(out["sets"][0]["won"], False)

    def test_path_skips_incomplete_and_byes(self):
        inc = mkset(1, 1, "R1", "A", self._mine(None), (11, "o", "", None), state=2)
        bye = mkset(2, 2, "R2", "B", self._mine(None), None, winner=7)
        out = build_player_path([inc, bye], 7, self._active(), "E", 1)
        self.assertEqual(out["sets"], [])

    def test_path_dq(self):
        s = mkset(1, 1, "R1", "A", self._mine(0), (11, "o", "", -1), winner=7, completed_at=1)
        out = build_player_path([s], 7, self._active(), "E", 1)
        self.assertEqual(out["sets"][0]["score"], "DQ")
        self.assertIs(out["sets"][0]["won"], True)

    def test_path_header(self):
        out = build_player_path([], "7", self._active(1), "Event", 55)
        self.assertEqual(out["player"], {"name": "Me", "team": "HH"})
        self.assertEqual(out["currentRound"], "Winners Final")
        self.assertEqual(out["eventName"], "Event")
        self.assertEqual(out["updatedAt"], 55)


if __name__ == "__main__":
    unittest.main()
