import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from startgg import (
    parse_slug,
    normalize_stream_queue,
    fetch_stream_queue,
    StreamQueueError,
    load_queue_config,
    save_queue_config,
    build_config_response,
)
import io
import json as _json
import tempfile
import urllib.error
from unittest import mock


SAMPLE_DATA = {
    "tournament": {
        "streamQueue": [
            {
                "stream": {"streamName": "Main Stage"},
                "sets": [
                    {
                        "fullRoundText": "Winners Final",
                        "round": 1,
                        "slots": [
                            {"entrant": {"name": "MrCosta"}},
                            {"entrant": {"name": "FGC Hamburg | Community"}},
                        ],
                    },
                    {
                        "fullRoundText": "Grand Final",
                        "round": 2,
                        "slots": [{"entrant": None}, {"entrant": None}],
                    },
                ],
            }
        ]
    }
}


class ParseSlugTests(unittest.TestCase):
    def test_full_url_with_details(self):
        url = "https://www.start.gg/tournament/test-tournament-do-not-publish-1/details"
        self.assertEqual(parse_slug(url), "test-tournament-do-not-publish-1")

    def test_trailing_slash(self):
        self.assertEqual(parse_slug("https://start.gg/tournament/foo/"), "foo")

    def test_query_string(self):
        self.assertEqual(parse_slug("https://start.gg/tournament/foo?x=1"), "foo")

    def test_bare_slug(self):
        self.assertEqual(parse_slug("my-slug"), "my-slug")

    def test_empty_returns_none(self):
        self.assertIsNone(parse_slug(""))
        self.assertIsNone(parse_slug(None))

    def test_url_without_tournament_returns_none(self):
        self.assertIsNone(parse_slug("https://start.gg/leaderboards"))


class NormalizeTests(unittest.TestCase):
    def test_maps_names_round_and_stream(self):
        stations = normalize_stream_queue(SAMPLE_DATA)
        self.assertEqual(len(stations), 1)
        self.assertEqual(stations[0]["streamName"], "Main Stage")
        first = stations[0]["sets"][0]
        self.assertEqual(first["fullRoundText"], "Winners Final")
        self.assertEqual(first["round"], 1)
        self.assertEqual(first["p1"], "MrCosta")
        self.assertEqual(first["p2"], "FGC Hamburg | Community")

    def test_null_entrants_become_none(self):
        stations = normalize_stream_queue(SAMPLE_DATA)
        gf = stations[0]["sets"][1]
        self.assertIsNone(gf["p1"])
        self.assertIsNone(gf["p2"])

    def test_missing_stream_name_falls_back(self):
        data = {"tournament": {"streamQueue": [{"sets": []}]}}
        stations = normalize_stream_queue(data)
        self.assertEqual(stations[0]["streamName"], "Stream")

    def test_null_queue_returns_empty_list(self):
        self.assertEqual(normalize_stream_queue({"tournament": {"streamQueue": None}}), [])
        self.assertEqual(normalize_stream_queue({}), [])
        self.assertEqual(normalize_stream_queue(None), [])

    def test_missing_slot_is_none(self):
        data = {
            "tournament": {
                "streamQueue": [
                    {"sets": [{"fullRoundText": "R1", "round": 1,
                               "slots": [{"entrant": {"name": "Solo"}}]}]}
                ]
            }
        }
        s = normalize_stream_queue(data)[0]["sets"][0]
        self.assertEqual(s["p1"], "Solo")
        self.assertIsNone(s["p2"])


class FetchTests(unittest.TestCase):
    def _fake_response(self, payload):
        return io.BytesIO(_json.dumps(payload).encode())

    def test_fetch_returns_normalized_stations(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=self._fake_response({"data": SAMPLE_DATA})):
            stations = fetch_stream_queue("some-slug", "tok")
        self.assertEqual(stations[0]["streamName"], "Main Stage")
        self.assertEqual(stations[0]["sets"][0]["p1"], "MrCosta")

    def test_graphql_errors_raise(self):
        payload = {"errors": [{"message": "Invalid authorization token"}]}
        with mock.patch("urllib.request.urlopen",
                        return_value=self._fake_response(payload)):
            with self.assertRaises(StreamQueueError):
                fetch_stream_queue("some-slug", "bad")

    def test_http_error_raises(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(StreamQueueError):
                fetch_stream_queue("some-slug", "bad")

    def test_network_error_raises(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("boom")):
            with self.assertRaises(StreamQueueError):
                fetch_stream_queue("some-slug", "tok")


class ConfigTests(unittest.TestCase):
    def test_load_missing_file_returns_defaults(self):
        cfg = load_queue_config("/nonexistent/path/streamqueue.json")
        self.assertEqual(cfg, {"slug": None, "streamName": None})

    def test_save_then_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "streamqueue.json")
            save_queue_config(path, {"slug": "foo", "streamName": "Main"})
            self.assertEqual(load_queue_config(path),
                             {"slug": "foo", "streamName": "Main"})

    def test_config_response_enabled_reflects_token(self):
        cfg = {"slug": "foo", "streamName": None}
        self.assertTrue(build_config_response("tok", 30, cfg)["enabled"])
        self.assertFalse(build_config_response("", 30, cfg)["enabled"])

    def test_config_response_never_includes_token(self):
        resp = build_config_response("secret-token", 30, {"slug": None, "streamName": None})
        self.assertNotIn("token", resp)
        self.assertNotIn("secret-token", resp.values())
        self.assertEqual(resp["refreshSeconds"], 30)


if __name__ == "__main__":
    unittest.main()
