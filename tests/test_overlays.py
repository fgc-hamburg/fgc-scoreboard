import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import overlays
from startgg import StreamQueueError
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

    def test_top8_reset_same_round_as_gf(self):
        sets = [s for s in full_bracket() if s["round"] != 5]
        sets.append(mkset(900, 5, "Grand Final", "GF", (1, "a", "", 2), (2, "b", "", 0), winner=1))
        sets.append(mkset(901, 5, "Grand Final Reset", "GR", (1, "a", "", 0), (2, "b", "", 0)))
        out = build_top8(sets, "T", "E", 1)
        self.assertEqual([m["setId"] for m in out["rounds"]["grandFinal"]], [900])
        self.assertEqual([m["setId"] for m in out["rounds"]["grandFinalReset"]], [901])
        wf = [s["id"] for s in sets if s["round"] == 4]
        self.assertEqual([m["setId"] for m in out["rounds"]["winnersFinal"]], wf)

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


class StateAndRefreshTests(unittest.TestCase):
    def test_merge_active_state_keeps_previous_when_set_null(self):
        prev = {"setId": 5, "p1Id": 1, "p2Id": 2, "pathSide": "p1"}
        out = overlays.merge_active_state(prev, {"setId": None, "pathSide": "p2"})
        self.assertEqual(out["setId"], 5)
        self.assertEqual(out["p1Id"], 1)
        self.assertEqual(out["pathSide"], "p2")

    def test_merge_active_state_replaces_and_sanitizes_side(self):
        prev = {"setId": 5, "p1Id": 1, "p2Id": 2, "pathSide": "p2", "eventId": None}
        out = overlays.merge_active_state(
            prev, {"setId": 9, "p1Id": 3, "p2Id": 4, "pathSide": "x"})
        self.assertEqual(out, {"setId": 9, "p1Id": 3, "p2Id": 4, "pathSide": "p1",
                               "eventId": None})

    def test_state_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            self.assertEqual(overlays.load_state(p), overlays.DEFAULT_STATE)
            st = {"setId": 1, "p1Id": 2, "p2Id": 3, "pathSide": "p2", "eventId": 4}
            overlays.save_state(p, st)
            self.assertEqual(overlays.load_state(p), st)

    def _ctx(self):
        return {"id": 9, "round": 4, "fullRoundText": "Winners Final",
                "slots": [_slot((10, "a", "", 0)), _slot((20, "b", "", 0))],
                "event": {"id": 7, "name": "SF6", "tournament": {"name": "Cup"}},
                "phaseGroup": {"id": 77}}

    def _state(self, side="p1"):
        return {"setId": 9, "p1Id": 10, "p2Id": 20, "pathSide": side}

    def test_refresh_path_writes_only_playerpath(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_set_context", return_value=self._ctx()), \
                mock.patch("startgg.fetch_phase_group_sets") as fp, \
                mock.patch("startgg.fetch_entrant_event_sets", return_value=[]) as fe:
            self.assertTrue(overlays.refresh_path(self._state("p2"), "tok", Path(d), 123, {}))
            self.assertTrue((Path(d) / "playerpath.json").exists())
            self.assertFalse((Path(d) / "top8.json").exists())
            fp.assert_not_called()
            self.assertEqual(fe.call_args[0][1], 20)

    def test_refresh_path_caches_context(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_set_context", return_value=self._ctx()) as fc, \
                mock.patch("startgg.fetch_entrant_event_sets", return_value=[]):
            cache = {}
            overlays.refresh_path(self._state(), "tok", Path(d), 1, cache)
            overlays.refresh_path(self._state(), "tok", Path(d), 2, cache)
            self.assertEqual(fc.call_count, 1)

    def test_refresh_path_error_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_set_context", return_value=self._ctx()), \
                mock.patch("startgg.fetch_entrant_event_sets",
                           side_effect=StreamQueueError("boom")):
            (Path(d) / "playerpath.json").write_text('{"old": true}')
            with self.assertRaises(StreamQueueError):
                overlays.refresh_path(self._state(), "tok", Path(d), 1, {})
            self.assertEqual(json.loads((Path(d) / "playerpath.json").read_text()), {"old": True})

    def test_refresh_path_noop_without_set(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_set_context") as fc:
            self.assertFalse(overlays.refresh_path(dict(overlays.DEFAULT_STATE), "tok", Path(d), 1, {}))
            fc.assert_not_called()
            self.assertEqual(list(Path(d).iterdir()), [])


def _event(id=7, name="Tekken 8", phases=None):
    if phases is None:
        phases = [{"id": 1, "phaseOrder": 1, "bracketType": "DOUBLE_ELIMINATION",
                   "phaseGroupIds": [77]}]
    return {"id": id, "name": name, "phases": phases}


def _tournament(*events):
    return {"tournamentName": "Cup", "events": list(events) or [_event()]}


class BracketPickTests(unittest.TestCase):
    def test_pick_bracket_phase_group_single(self):
        self.assertEqual(overlays.pick_bracket_phase_group(_event()), 77)

    def test_pick_bracket_phase_group_highest_de_phase(self):
        ev = _event(phases=[
            {"id": 1, "phaseOrder": 1, "bracketType": "DOUBLE_ELIMINATION", "phaseGroupIds": [11]},
            {"id": 2, "phaseOrder": 3, "bracketType": "ROUND_ROBIN", "phaseGroupIds": [33]},
            {"id": 3, "phaseOrder": 2, "bracketType": "DOUBLE_ELIMINATION", "phaseGroupIds": [22]},
        ])
        self.assertEqual(overlays.pick_bracket_phase_group(ev), 22)

    def test_pick_bracket_phase_group_none_raises(self):
        ev = _event(phases=[{"id": 1, "phaseOrder": 1, "bracketType": "SINGLE_ELIMINATION",
                             "phaseGroupIds": [1]}])
        with self.assertRaisesRegex(StreamQueueError, "No single double-elimination bracket"):
            overlays.pick_bracket_phase_group(ev)

    def test_pick_bracket_phase_group_pools_raises(self):
        ev = _event(phases=[{"id": 1, "phaseOrder": 1, "bracketType": "DOUBLE_ELIMINATION",
                             "phaseGroupIds": [1, 2]}])
        with self.assertRaisesRegex(StreamQueueError, "No single double-elimination bracket"):
            overlays.pick_bracket_phase_group(ev)

    def test_resolve_event_single_auto(self):
        ev = _event()
        self.assertIs(overlays.resolve_event([ev], None), ev)

    def test_resolve_event_by_id_string_vs_int(self):
        a, b = _event(1, "A"), _event(2, "B")
        self.assertIs(overlays.resolve_event([a, b], "2"), b)
        self.assertIs(overlays.resolve_event([a, b], 1), a)

    def test_resolve_event_errors(self):
        a, b = _event(1), _event(2)
        with self.assertRaisesRegex(StreamQueueError, "Choose an event for the top 8"):
            overlays.resolve_event([a, b], None)
        with self.assertRaisesRegex(StreamQueueError, "Selected event not found"):
            overlays.resolve_event([a, b], 9)

    def test_set_event_and_merge_preserves_event_id(self):
        prev = dict(overlays.DEFAULT_STATE)
        self.assertIsNone(prev["eventId"])
        prev = overlays.set_event(prev, 5)
        self.assertEqual(prev["eventId"], 5)
        out = overlays.merge_active_state(prev, {"setId": 9, "p1Id": 1, "p2Id": 2})
        self.assertEqual(out["eventId"], 5)
        out = overlays.merge_active_state(prev, {"setId": None})
        self.assertEqual(out["eventId"], 5)


class Top8RefreshTests(unittest.TestCase):
    def test_refresh_top8_writes_with_tournament_name(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_tournament_events", return_value=_tournament()), \
                mock.patch("startgg.fetch_phase_group_sets", return_value=full_bracket()) as fp:
            self.assertTrue(overlays.refresh_top8("cup", None, "tok", Path(d), 5, {}))
            top = json.loads((Path(d) / "top8.json").read_text())
            self.assertEqual(top["tournamentName"], "Cup")
            self.assertEqual(top["eventName"], "Tekken 8")
            self.assertEqual(fp.call_args[0][0], 77)

    def test_refresh_top8_noop_without_slug(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_tournament_events") as fe, \
                mock.patch("startgg.fetch_phase_group_sets") as fp:
            self.assertFalse(overlays.refresh_top8(None, None, "tok", Path(d), 5, {}))
            fe.assert_not_called()
            fp.assert_not_called()
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_refresh_top8_requires_event_choice_when_multiple(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_tournament_events",
                           return_value=_tournament(_event(1), _event(2))), \
                mock.patch("startgg.fetch_phase_group_sets") as fp:
            (Path(d) / "top8.json").write_text('{"old": true}')
            with self.assertRaisesRegex(StreamQueueError, "Choose an event for the top 8"):
                overlays.refresh_top8("cup", None, "tok", Path(d), 5, {})
            fp.assert_not_called()
            self.assertEqual(json.loads((Path(d) / "top8.json").read_text()), {"old": True})

    def test_refresh_top8_cache_keyed_by_slug_and_event(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_tournament_events", return_value=_tournament()) as fe, \
                mock.patch("startgg.fetch_phase_group_sets", return_value=[]):
            cache = {}
            overlays.refresh_top8("a", None, "tok", Path(d), 1, cache)
            overlays.refresh_top8("a", None, "tok", Path(d), 2, cache)
            self.assertEqual(fe.call_count, 1)
            overlays.refresh_top8("b", None, "tok", Path(d), 3, cache)
            self.assertEqual(fe.call_count, 2)
            overlays.refresh_top8("b", 7, "tok", Path(d), 4, cache)
            self.assertEqual(fe.call_count, 3)

    def test_refresh_top8_error_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_tournament_events", return_value=_tournament()), \
                mock.patch("startgg.fetch_phase_group_sets",
                           side_effect=StreamQueueError("boom")):
            (Path(d) / "top8.json").write_text('{"old": true}')
            with self.assertRaises(StreamQueueError):
                overlays.refresh_top8("cup", None, "tok", Path(d), 5, {})
            self.assertEqual(json.loads((Path(d) / "top8.json").read_text()), {"old": True})


class RefreshAllTests(unittest.TestCase):
    STATE = {"setId": 9, "p1Id": 10, "p2Id": 20, "pathSide": "p1", "eventId": None}

    def _ctx(self):
        return StateAndRefreshTests._ctx(None)

    def test_refresh_jobs_are_independent(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_tournament_events", return_value=_tournament()), \
                mock.patch("startgg.fetch_phase_group_sets", return_value=full_bracket()), \
                mock.patch("startgg.fetch_set_context", side_effect=StreamQueueError("path boom")):
            res = overlays.refresh_all(self.STATE, "cup", "tok", Path(d), 5,
                                       {"top8": {}, "path": {}})
            self.assertIs(res["top8"], True)
            self.assertIn("path boom", res["path"])
            self.assertTrue((Path(d) / "top8.json").exists())
            self.assertFalse((Path(d) / "playerpath.json").exists())
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("startgg.fetch_tournament_events",
                           side_effect=StreamQueueError("top boom")), \
                mock.patch("startgg.fetch_set_context", return_value=self._ctx()), \
                mock.patch("startgg.fetch_entrant_event_sets", return_value=[]):
            res = overlays.refresh_all(self.STATE, "cup", "tok", Path(d), 5,
                                       {"top8": {}, "path": {}})
            self.assertIn("top boom", res["top8"])
            self.assertIs(res["path"], True)
            self.assertFalse((Path(d) / "top8.json").exists())
            self.assertTrue((Path(d) / "playerpath.json").exists())

    def test_refresh_all_idle(self):
        with tempfile.TemporaryDirectory() as d:
            res = overlays.refresh_all(dict(overlays.DEFAULT_STATE), None, "tok", Path(d), 5,
                                       {"top8": {}, "path": {}})
            self.assertEqual(res, {"top8": False, "path": False})


class NullRoundTests(unittest.TestCase):
    def test_top8_ignores_null_round(self):
        sets = full_bracket() + [mkset(999, None, "Weird", "ZZ")]
        out = build_top8(sets, "T", "E", 1)
        self.assertEqual(set(out["rounds"]), set(ROUND_KEYS))


if __name__ == "__main__":
    unittest.main()
