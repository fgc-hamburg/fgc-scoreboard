import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets.server

from detector_client import (
    DEFAULT_DETECTOR_CONFIG,
    DetectorClient,
    load_detector_config,
    save_detector_config,
)


class TestDetectorConfig(unittest.TestCase):
    def test_load_missing_file_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "detectorconfig.json")
            self.assertEqual(load_detector_config(path), DEFAULT_DETECTOR_CONFIG)

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "detectorconfig.json")
            save_detector_config(
                path, {"host": "localhost", "port": 8765, "autoSubmit": True}
            )
            self.assertEqual(
                load_detector_config(path),
                {"host": "localhost", "port": 8765, "autoSubmit": True},
            )

    def test_load_corrupt_file_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "detectorconfig.json")
            with open(path, "w") as f:
                f.write("not json")
            self.assertEqual(load_detector_config(path), DEFAULT_DETECTOR_CONFIG)


def _free_port():
    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FakeDetectorServer:
    """A throwaway websockets server the tests drive DetectorClient against."""

    def __init__(self):
        self.port = _free_port()
        self._loop = None
        self._thread = None
        self._server = None
        self._clients = []
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        async def handler(ws):
            self._clients.append(ws)
            try:
                async for _ in ws:
                    pass
            finally:
                if ws in self._clients:
                    self._clients.remove(ws)

        self._server = await websockets.server.serve(handler, "localhost", self.port)
        self._loop.call_soon(self._ready.set)
        await self._server.wait_closed()

    def send_to_all(self, message):
        for ws in list(self._clients):
            asyncio.run_coroutine_threadsafe(ws.send(message), self._loop)

    def drop_all_clients(self):
        for ws in list(self._clients):
            asyncio.run_coroutine_threadsafe(ws.close(), self._loop)

    def stop(self):
        if self._server is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread is not None:
            self._thread.join(timeout=5)


def _wait_until(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestDetectorClientConnection(unittest.TestCase):
    def test_connect_receives_match_end_event(self):
        server = _FakeDetectorServer()
        server.start()
        try:
            client = DetectorClient()
            client.connect("localhost", server.port)
            self.assertTrue(_wait_until(lambda: client.status()["connected"]))

            server.send_to_all(json.dumps({
                "type": "match_end", "game": "sf6", "winner": "p1",
                "confidence": 0.94, "ts": "2026-08-01T10:00:00Z",
            }))

            events = []
            deadline = time.time() + 5
            while time.time() < deadline and not events:
                events = client.drain_events()
                if not events:
                    time.sleep(0.05)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["game"], "sf6")
            self.assertEqual(events[0]["winner"], "p1")
            self.assertEqual(events[0]["confidence"], 0.94)
            self.assertEqual(client.drain_events(), [])  # second drain is empty
        finally:
            client.disconnect()
            server.stop()

    def test_connect_to_unreachable_host_sets_error_and_does_not_retry(self):
        client = DetectorClient()
        client.connect("localhost", _free_port())  # nothing listening
        self.assertTrue(_wait_until(lambda: client.status()["error"] is not None))
        status = client.status()
        self.assertFalse(status["connected"])
        self.assertIsNotNone(status["error"])
        client.disconnect()

    def test_reconnects_after_unexpected_drop(self):
        server = _FakeDetectorServer()
        server.start()
        try:
            client = DetectorClient()
            client.connect("localhost", server.port)
            self.assertTrue(_wait_until(lambda: client.status()["connected"]))

            server.drop_all_clients()
            self.assertTrue(_wait_until(lambda: not client.status()["connected"]))
            self.assertTrue(_wait_until(lambda: client.status()["connected"],
                                         timeout=10))
        finally:
            client.disconnect()
            server.stop()

    def test_disconnect_stops_reconnect_attempts(self):
        server = _FakeDetectorServer()
        server.start()
        try:
            client = DetectorClient()
            client.connect("localhost", server.port)
            self.assertTrue(_wait_until(lambda: client.status()["connected"]))
            client.disconnect()
            status = client.status()
            self.assertFalse(status["connected"])
            self.assertIsNone(status["host"])
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
