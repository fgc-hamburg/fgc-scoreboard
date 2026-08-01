# Match-Detector Event Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the fgc-scoreboard dashboard connect to the fgc-stream-event-detector's WebSocket
server, receive `match_end` events, and apply them to the loaded match's score — manually
(dirty flag, operator clicks Submit) or automatically (auto-submits on receipt).

**Architecture:** A new `detector_client.py` module runs a `websockets` client in a background
daemon thread with its own asyncio loop, decoupled from `server.py`'s synchronous
`HTTPServer`. It collects `match_end` events into a lock-protected in-memory list. `server.py`
exposes HTTP endpoints that let `control.html` connect/disconnect, poll connection status, poll
for new events (mailbox semantics — GET drains the list), and toggle auto-submit. `control.html`
applies each drained event through the *existing* `adjustScore()` / `submitData()` functions, so
no new score-mutation code path is introduced.

**Tech Stack:** Python 3 stdlib `http.server` (unchanged) + `websockets` (new dependency, scoped
to `detector_client.py`) for the WebSocket client. Plain JS in `control.html` (unchanged
pattern). `unittest` for tests, matching `tests/test_startgg.py`.

## Global Constraints

- `websockets` is the *only* new dependency, used *only* inside `detector_client.py`.
  `server.py`'s HTTP handling stays stdlib-only (per the design doc's explicit non-goal of
  disturbing that property elsewhere).
- No confidence threshold — any `match_end` received is trusted and applied.
- Game-mismatch events are dropped silently except for a transient operator-visible message —
  never applied to the wrong game's score.
- `adjustScore(player, 1)` and `submitData()` are reused unmodified for applying an event —
  do not write a parallel score-mutation path.
- `/api/detector/events` has mailbox (drain-on-GET) semantics — single consumer assumption,
  explicitly accepted in the design doc.
- Config persists to `sc/detectorconfig.json`, gitignored like `sc/streamqueue.json`.

---

## File Structure

- **Create `detector_client.py`** (repo root, sibling to `startgg.py`) — `DetectorClient` class
  (background WebSocket client + thread-safe status/event queue) and
  `load_detector_config` / `save_detector_config` functions (mirrors `startgg.py`'s
  `load_queue_config` / `save_queue_config` pattern).
- **Create `tests/test_detector_client.py`** — spins up a throwaway local `websockets` server
  per test and drives `DetectorClient` against it.
- **Create `requirements.txt`** — pins `websockets`.
- **Modify `server.py`** — new `/api/detector/*` endpoints, a module-level `DetectorClient`
  instance, startup auto-connect from persisted config.
- **Modify `control.html`** — new "Match Detector" panel (HTML + CSS following the existing
  Stream Queue panel's patterns), a new `AVATAR` game option, and JS wiring
  (connect/disconnect, status+event polling, game-code mapping, apply-via-existing-functions).
- **Modify `.gitignore`** — add `sc/detectorconfig.json`.
- **Modify `README.md`** — document the new dependency and the Match Detector panel.
- **Modify `Makefile`** — add a `setup` target that creates `.venv` and installs
  `requirements.txt`, and update `run-dashboard` to use the venv's Python when present.

---

## Task 1: `DetectorClient` — connect, status, and message collection

**Files:**
- Create: `detector_client.py`
- Test: `tests/test_detector_client.py`

**Interfaces:**
- Produces: `DetectorClient` with methods `connect(host: str, port: int) -> None`,
  `disconnect() -> None`, `status() -> dict` (`{"connected": bool, "host": str|None,
  "port": int|None, "error": str|None}`), `drain_events() -> list[dict]` (each event
  `{"game": str, "winner": str, "confidence": float, "ts": str}`).
- Produces: `load_detector_config(path) -> dict` (`{"host", "port", "autoSubmit"}`),
  `save_detector_config(path, cfg) -> None`, `DEFAULT_DETECTOR_CONFIG` dict constant.

- [ ] **Step 1: Write the failing tests for config load/save**

Create `tests/test_detector_client.py`:

```python
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_detector_client -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'detector_client'`

- [ ] **Step 3: Write `detector_client.py` config functions**

```python
"""WebSocket client for the fgc-stream-event-detector.

Runs its own asyncio event loop in a background thread, fully decoupled from
server.py's synchronous HTTP handling. Collects match_end events into an
in-memory, lock-protected list that the dashboard drains by polling — see
docs/superpowers/specs/2026-08-01-detector-event-consumer-design.md.
"""

import asyncio
import json
import threading

import websockets

DEFAULT_DETECTOR_CONFIG = {"host": None, "port": None, "autoSubmit": False}


def load_detector_config(path):
    """Read the persisted host/port/autoSubmit, defaulting on any problem."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return dict(DEFAULT_DETECTOR_CONFIG)
    return {
        "host": data.get("host"),
        "port": data.get("port"),
        "autoSubmit": bool(data.get("autoSubmit", False)),
    }


def save_detector_config(path, cfg):
    out = {
        "host": cfg.get("host"),
        "port": cfg.get("port"),
        "autoSubmit": bool(cfg.get("autoSubmit", False)),
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_detector_client -v`
Expected: `test_load_missing_file_returns_default`, `test_save_then_load_roundtrips`,
`test_load_corrupt_file_returns_default` all PASS.

- [ ] **Step 5: Commit**

```bash
git add detector_client.py tests/test_detector_client.py
git commit -m "feat: add detector config load/save"
```

- [ ] **Step 6: Write the failing test for connect + status + event collection**

Append to `tests/test_detector_client.py`:

```python
import asyncio
import json
import threading
import time

import websockets
import websockets.server


def _free_port():
    import socket

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

            self.assertTrue(_wait_until(lambda: client.drain_events() != []
                                         or client._events))
            # drain_events above may have already consumed it in the predicate;
            # re-check via a second connect-status read is not needed — assert
            # the queue is empty now (already drained) or drain returns it once.
            events = client.drain_events()
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
```

Fix the flaky assertion in `test_connect_receives_match_end_event` (drain_events is not
idempotent to call twice inside a predicate) by replacing the `_wait_until` call with a direct
poll loop:

```python
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
```

- [ ] **Step 7: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_detector_client -v`
Expected: FAIL — `DetectorClient` has no `connect`/`status`/`drain_events`/`disconnect`
(ImportError or AttributeError).

- [ ] **Step 8: Implement `DetectorClient`**

Append to `detector_client.py`:

```python
class DetectorClient:
    """Background WebSocket client that collects match_end events.

    `status()` and `drain_events()` are called from the HTTP handler thread;
    `connect()`/`disconnect()` too. All mutable state is guarded by `_lock`.
    The initial connect attempt does not auto-retry on failure — only a drop
    *after* a successful connection triggers the backoff reconnect loop. See
    the design doc's Failure handling section.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._host = None
        self._port = None
        self._connected = False
        self._error = None
        self._events = []
        self._loop = None
        self._thread = None
        self._generation = 0

    def status(self):
        with self._lock:
            return {
                "connected": self._connected,
                "host": self._host,
                "port": self._port,
                "error": self._error,
            }

    def drain_events(self):
        with self._lock:
            events, self._events = self._events, []
        return events

    def connect(self, host, port):
        self.disconnect()
        with self._lock:
            self._host = host
            self._port = port
            self._error = None
            self._generation += 1
            generation = self._generation
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=self._run_loop, args=(loop, host, port, generation), daemon=True
        )
        with self._lock:
            self._loop = loop
            self._thread = thread
        thread.start()

    def disconnect(self):
        with self._lock:
            self._generation += 1
            self._host = None
            self._port = None
            self._connected = False
            self._error = None
            loop = self._loop
            self._loop = None
            self._thread = None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    def _run_loop(self, loop, host, port, generation):
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect_loop(loop, host, port, generation))
        finally:
            loop.close()

    def _is_current(self, generation):
        with self._lock:
            return generation == self._generation

    async def _connect_loop(self, loop, host, port, generation):
        backoff = 1
        ever_connected = False
        while self._is_current(generation):
            try:
                async with websockets.connect(f"ws://{host}:{port}") as ws:
                    if not self._is_current(generation):
                        return
                    with self._lock:
                        self._connected = True
                        self._error = None
                    ever_connected = True
                    backoff = 1
                    async for raw in ws:
                        if not self._is_current(generation):
                            return
                        self._handle_message(raw)
                    if not self._is_current(generation):
                        return
                    with self._lock:
                        self._connected = False
                        self._error = "connection closed"
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                if not self._is_current(generation):
                    return
                with self._lock:
                    self._connected = False
                    self._error = str(exc)

            if not ever_connected or not self._is_current(generation):
                return  # initial connect failed — no auto-retry

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _handle_message(self, raw):
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        if not isinstance(payload, dict) or payload.get("type") != "match_end":
            return
        with self._lock:
            self._events.append({
                "game": payload.get("game"),
                "winner": payload.get("winner"),
                "confidence": payload.get("confidence"),
                "ts": payload.get("ts"),
            })
```

- [ ] **Step 9: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_detector_client -v`
Expected: all tests PASS, including `test_reconnects_after_unexpected_drop` (may take a few
seconds due to the 1s initial backoff).

- [ ] **Step 10: Commit**

```bash
git add detector_client.py tests/test_detector_client.py
git commit -m "feat: add DetectorClient background WebSocket client"
```

---

## Task 2: Dependency + venv setup

**Files:**
- Create: `requirements.txt`
- Modify: `Makefile`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: `.venv/bin/python` available for `make setup` users; `requirements.txt` pins
  `websockets`.

- [ ] **Step 1: Create `requirements.txt`**

```
websockets>=12
```

- [ ] **Step 2: Add `sc/detectorconfig.json` to `.gitignore`**

Edit `.gitignore`, adding a line after `sc/streamqueue.json`:

```
sc/streamqueue.json
sc/detectorconfig.json
```

- [ ] **Step 3: Add a `setup` target and use the venv in `run-dashboard`**

Replace the contents of `Makefile`:

```makefile
.PHONY: run-dashboard setup

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run-dashboard:
	.venv/bin/python server.py
```

- [ ] **Step 4: Update README's dependency claim**

In `README.md`, replace:

```
Then open **http://localhost:8080** in any browser. No dependencies required — pure Python stdlib.
```

with:

```
Then open **http://localhost:8080** in any browser. The dashboard itself needs no dependencies
beyond the Python standard library. The optional match-detector integration (see below) needs
`websockets`, installed via `make setup`.
```

- [ ] **Step 5: Verify the venv builds clean from scratch**

Run: `rm -rf .venv && make setup && .venv/bin/python -c "import websockets; import detector_client; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt Makefile .gitignore README.md
git commit -m "chore: add websockets dependency and venv setup for detector client"
```

---

## Task 3: `server.py` — detector HTTP endpoints

**Files:**
- Modify: `server.py`
- Test: manual (see Step 6) — `server.py`'s `Handler` is stdlib `BaseHTTPRequestHandler` with no
  existing test harness; `test_startgg.py` only tests `startgg.py`'s pure functions. This task
  follows that existing precedent and is verified by manual `curl` calls, matching how the
  stream-queue endpoints were verified.

**Interfaces:**
- Consumes: `detector_client.DetectorClient`, `detector_client.load_detector_config`,
  `detector_client.save_detector_config` from Task 1.
- Produces: `GET /api/detector/status`, `POST /api/detector/connect`,
  `POST /api/detector/disconnect`, `POST /api/detector/auto_submit`,
  `GET /api/detector/events` — used by Task 4's `control.html`.

- [ ] **Step 1: Add the import and module-level state**

In `server.py`, after `import startgg`:

```python
import detector_client
```

After `QUEUE_CONFIG_PATH = BASE_DIR / "sc" / "streamqueue.json"`:

```python
DETECTOR_CONFIG_PATH = BASE_DIR / "sc" / "detectorconfig.json"

detector = detector_client.DetectorClient()
DETECTOR_STATE = {"autoSubmit": False}
```

- [ ] **Step 2: Route the new endpoints**

In `do_GET`, add before the final `else`:

```python
        elif self.path == "/api/detector/status":
            self._handle_get_detector_status()
        elif self.path == "/api/detector/events":
            self._handle_get_detector_events()
```

In `do_POST`, add before the final `else`:

```python
        elif self.path == "/api/detector/connect":
            self._handle_post_detector_connect()
        elif self.path == "/api/detector/disconnect":
            self._handle_post_detector_disconnect()
        elif self.path == "/api/detector/auto_submit":
            self._handle_post_detector_auto_submit()
```

- [ ] **Step 3: Implement the handler methods**

Add after `_handle_post_clear` (end of the "Stream queue" section):

```python
    # ---------- Match detector ----------

    def _detector_status_payload(self):
        status = detector.status()
        status["autoSubmit"] = DETECTOR_STATE["autoSubmit"]
        return status

    def _handle_get_detector_status(self):
        self.send_json(200, self._detector_status_payload())

    def _handle_get_detector_events(self):
        self.send_json(200, {"events": detector.drain_events()})

    def _handle_post_detector_connect(self):
        try:
            body = self._read_json_body()
        except ValueError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        host = (body.get("host") or "").strip()
        if not host:
            self.send_json(400, {"error": "host is required"})
            return
        try:
            port = int(body.get("port"))
        except (TypeError, ValueError):
            self.send_json(400, {"error": "port must be an integer"})
            return
        detector.connect(host, port)
        detector_client.save_detector_config(DETECTOR_CONFIG_PATH, {
            "host": host, "port": port, "autoSubmit": DETECTOR_STATE["autoSubmit"],
        })
        self.send_json(200, self._detector_status_payload())

    def _handle_post_detector_disconnect(self):
        detector.disconnect()
        detector_client.save_detector_config(DETECTOR_CONFIG_PATH, {
            "host": None, "port": None, "autoSubmit": DETECTOR_STATE["autoSubmit"],
        })
        self.send_json(200, self._detector_status_payload())

    def _handle_post_detector_auto_submit(self):
        try:
            body = self._read_json_body()
        except ValueError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        DETECTOR_STATE["autoSubmit"] = bool(body.get("enabled"))
        current = detector.status()
        detector_client.save_detector_config(DETECTOR_CONFIG_PATH, {
            "host": current["host"], "port": current["port"],
            "autoSubmit": DETECTOR_STATE["autoSubmit"],
        })
        self.send_json(200, self._detector_status_payload())
```

- [ ] **Step 4: Auto-connect from persisted config at startup**

Replace the `if __name__ == "__main__":` block at the bottom of `server.py`:

```python
if __name__ == "__main__":
    _detector_cfg = detector_client.load_detector_config(DETECTOR_CONFIG_PATH)
    DETECTOR_STATE["autoSubmit"] = _detector_cfg["autoSubmit"]
    if _detector_cfg["host"] and _detector_cfg["port"]:
        detector.connect(_detector_cfg["host"], _detector_cfg["port"])

    server = HTTPServer(("", PORT), Handler)
    print(f"Control dashboard running at http://localhost:{PORT}")
    print(f"  JSON file: {JSON_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
```

- [ ] **Step 5: Sanity-check the module imports and starts**

Run: `.venv/bin/python -c "import server"`
Expected: no output, no traceback (importing must not start the HTTP server or auto-connect,
since that only happens inside the `__main__` guard).

- [ ] **Step 6: Manual endpoint verification**

Run: `.venv/bin/python server.py &` then, in the same shell:

```bash
curl -s http://localhost:8080/api/detector/status
curl -s -X POST http://localhost:8080/api/detector/connect \
  -H 'Content-Type: application/json' -d '{"host":"localhost","port":1}'
sleep 1
curl -s http://localhost:8080/api/detector/status
curl -s -X POST http://localhost:8080/api/detector/auto_submit \
  -H 'Content-Type: application/json' -d '{"enabled":true}'
curl -s -X POST http://localhost:8080/api/detector/disconnect
curl -s http://localhost:8080/api/detector/events
kill %1
```

Expected: first status shows `connected: false, host: null`; after connect, status eventually
shows `connected: false, error: <something about connection refused>` (port 1 has nothing
listening) with `host: "localhost", port: 1`; auto_submit response shows `autoSubmit: true`;
disconnect response shows `host: null, connected: false`; events response shows
`{"events": []}`.

- [ ] **Step 7: Commit**

```bash
git add server.py
git commit -m "feat: add /api/detector/* endpoints to server.py"
```

---

## Task 4: `control.html` — Match Detector panel

**Files:**
- Modify: `control.html`

**Interfaces:**
- Consumes: `GET /api/detector/status`, `POST /api/detector/connect`,
  `POST /api/detector/disconnect`, `POST /api/detector/auto_submit`,
  `GET /api/detector/events` from Task 3. Reuses the existing `adjustScore(player, delta)`,
  `submitData()`, `showStatus(msg, type)`, `state` object, and `escapeHtml()` already defined in
  `control.html`.
- Produces: nothing consumed elsewhere — this is the last task.

- [ ] **Step 1: Add the `AVATAR` game option**

In the `<select id="game">` block (around line 293-311), add a new option in alphabetical
order between `2XKO` and `BBCF`:

```html
          <option value="2XKO">2XKO</option>
          <option value="AVATAR">AVATAR</option>
          <option value="BBCF">BBCF</option>
```

- [ ] **Step 2: Add CSS for the Match Detector panel**

After the `.btn-report:disabled` rule (end of the Stream Queue CSS block), add:

```css
    /* Match Detector */
    .detector-section {
      background: #252525;
      border: 1px solid #3a3a3a;
      border-radius: 8px;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .detector-header { display: flex; justify-content: space-between; align-items: center; }
    .detector-header h2 {
      font-size: 0.85rem; text-transform: uppercase;
      letter-spacing: 0.08em; color: #888;
    }
    .detector-status { display: flex; align-items: center; gap: 0.5rem; font-size: 0.82rem; color: #aaa; }
    .detector-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: #5a2020; flex-shrink: 0;
    }
    .detector-dot.connected { background: #5cb85c; }
    .detector-form { display: flex; gap: 0.75rem; align-items: flex-end; }
    .detector-form label { flex: 1; }
    .detector-form input[type="text"] { width: 100%; }
    .detector-error { color: #d9534f; font-size: 0.82rem; }
    .detector-error:empty { display: none; }
    .detector-auto {
      display: flex; align-items: center; gap: 0.5rem;
      font-size: 0.82rem; color: #aaa;
    }
```

- [ ] **Step 3: Add the panel HTML**

After the closing `</div>` of `queue-section` (end of the Stream Queue block, before the
`</div>` that closes `.panel`), add:

```html
    <!-- Match Detector -->
    <div class="detector-section">
      <div class="detector-header">
        <h2>Match Detector</h2>
        <div class="detector-status">
          <span class="detector-dot" id="detectorDot"></span>
          <span id="detectorStatusText">Disconnected</span>
        </div>
      </div>

      <div class="detector-form" id="detectorSetup">
        <label>
          Host
          <input type="text" id="detectorHost" placeholder="localhost">
        </label>
        <label style="flex: 0 0 6rem;">
          Port
          <input type="text" id="detectorPort" placeholder="8765">
        </label>
        <button class="btn-load" id="detectorConnectBtn" onclick="connectDetector()">Connect</button>
      </div>
      <div class="detector-form" id="detectorConnected" style="display:none;">
        <button class="btn-change" onclick="disconnectDetector()">Disconnect</button>
        <label class="detector-auto">
          <input type="checkbox" id="detectorAutoSubmit" onchange="toggleAutoSubmit()">
          Auto-submit on receipt
        </label>
      </div>

      <div class="detector-error" id="detectorError"></div>
    </div>
```

- [ ] **Step 4: Add the JS — state, init, connect/disconnect, auto-submit toggle**

After the `// ---------- Stream Queue ----------` block's helper functions and before
`init();` at the bottom of the `<script>`, add:

```js
    // ---------- Match Detector ----------
    const DETECTOR_GAME_MAP = { sf6: "SF6", tekken8: "TEKKEN8", avatar: "AVATAR" };
    let detectorPollTimer = null;

    async function initDetector() {
      await refreshDetectorStatus();
      if (detectorPollTimer) clearInterval(detectorPollTimer);
      detectorPollTimer = setInterval(pollDetector, 2000);
    }

    async function pollDetector() {
      await refreshDetectorStatus();
      await pollDetectorEvents();
    }

    async function refreshDetectorStatus() {
      try {
        const res = await fetch("/api/detector/status");
        const status = await res.json();
        renderDetectorStatus(status);
      } catch (_) {}
    }

    function renderDetectorStatus(status) {
      const dot = document.getElementById("detectorDot");
      const text = document.getElementById("detectorStatusText");
      dot.classList.toggle("connected", !!status.connected);
      text.textContent = status.connected
        ? `Connected to ${status.host}:${status.port}`
        : (status.host ? `Connecting to ${status.host}:${status.port}...` : "Disconnected");

      document.getElementById("detectorSetup").style.display = status.host ? "none" : "flex";
      document.getElementById("detectorConnected").style.display = status.host ? "flex" : "none";
      document.getElementById("detectorAutoSubmit").checked = !!status.autoSubmit;
      document.getElementById("detectorError").textContent = status.error || "";
    }

    async function connectDetector() {
      const host = document.getElementById("detectorHost").value.trim();
      const port = document.getElementById("detectorPort").value.trim();
      if (!host || !port) return;
      try {
        const res = await fetch("/api/detector/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host, port: Number(port) }),
        });
        const status = await res.json();
        if (!res.ok) { showStatus("Detector connect failed: " + (status.error || res.status), "err"); return; }
        renderDetectorStatus(status);
      } catch (e) {
        showStatus("Detector connect failed: " + e.message, "err");
      }
    }

    async function disconnectDetector() {
      try {
        const res = await fetch("/api/detector/disconnect", { method: "POST" });
        const status = await res.json();
        renderDetectorStatus(status);
      } catch (e) {
        showStatus("Detector disconnect failed: " + e.message, "err");
      }
    }

    async function toggleAutoSubmit() {
      const enabled = document.getElementById("detectorAutoSubmit").checked;
      try {
        const res = await fetch("/api/detector/auto_submit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        });
        const status = await res.json();
        renderDetectorStatus(status);
      } catch (e) {
        showStatus("Auto-submit toggle failed: " + e.message, "err");
      }
    }

    async function pollDetectorEvents() {
      let data;
      try {
        const res = await fetch("/api/detector/events");
        data = await res.json();
      } catch (_) { return; }
      const events = data.events || [];
      for (const event of events) {
        await applyDetectorEvent(event);
      }
    }

    async function applyDetectorEvent(event) {
      const mappedGame = DETECTOR_GAME_MAP[event.game];
      if (!mappedGame || mappedGame !== state.game) {
        showStatus(
          `Ignored ${event.winner} win — event was for ${event.game}, loaded game is ${state.game}`,
          "err"
        );
        return;
      }
      const player = event.winner === "p1" ? 1 : event.winner === "p2" ? 2 : null;
      if (!player) return;
      adjustScore(player, 1);
      const autoSubmit = document.getElementById("detectorAutoSubmit").checked;
      if (autoSubmit) {
        await submitData();
      }
    }
```

- [ ] **Step 5: Call `initDetector()` from `init()`**

In the `init()` function near the top of the script, change:

```js
    async function init() {
      await loadRounds();
      await loadData(true);
      setInterval(() => loadData(false), 2000);
      initQueue();
    }
```

to:

```js
    async function init() {
      await loadRounds();
      await loadData(true);
      setInterval(() => loadData(false), 2000);
      initQueue();
      initDetector();
    }
```

- [ ] **Step 6: Manual browser verification**

Run: `.venv/bin/python server.py`, open `http://localhost:8080`.

Expected, checked by hand:
1. Match Detector panel shows "Disconnected", host/port fields, Connect button.
2. Enter an unreachable host/port (e.g. `localhost` / `1`), click Connect — status text
   flips to "Connecting to localhost:1..." then an error appears in `detectorError` within a
   few seconds; Disconnect button + Auto-submit checkbox are visible (host is set even though
   not connected yet).
3. Click Disconnect — form reverts to the host/port + Connect view, error clears.
4. Selecting `AVATAR` in the Game dropdown works like any other game (no 2nd-player row shown).

- [ ] **Step 7: End-to-end verification against a stub detector**

Write a throwaway stub (not committed) to confirm the full flow, e.g.:

```bash
.venv/bin/python - <<'EOF'
import asyncio, json
import websockets.server

async def handler(ws):
    await asyncio.sleep(2)
    await ws.send(json.dumps({
        "type": "match_end", "game": "sf6", "winner": "p1",
        "confidence": 0.94, "ts": "2026-08-01T10:00:00Z",
    }))
    await asyncio.Future()

async def main():
    async with websockets.server.serve(handler, "localhost", 8765):
        await asyncio.Future()

asyncio.run(main())
EOF
```

With the dashboard open and its Game set to `SF6`, connect to `localhost:8765`. Expected: ~2s
after connecting, P1's score increments by 1, the dirty dot lights up, and (with auto-submit
off) the score stays unsubmitted until Submit is clicked; toggling auto-submit on and
reconnecting applies the same event with an automatic Submit (status shows "Submitted.").
Also verify: set Game to `TEKKEN8` before the event fires — the score must NOT change, and a
"Ignored p1 win..." message appears.

- [ ] **Step 8: Commit**

```bash
git add control.html
git commit -m "feat: add Match Detector panel to control dashboard"
```

---

## Self-review notes

- **Spec coverage:** `DetectorClient` (connect/disconnect/status/drain_events) → Task 1.
  Persistence to `sc/detectorconfig.json` → Task 1 (functions) + Task 3 (wiring) + Task 2
  (gitignore). New dependency scoped to one module → Task 2 constraint + Global Constraints.
  All five endpoints → Task 3. Game-code mapping + `AVATAR` option → Task 4 Steps 1 and 4.
  Reuse of `adjustScore`/`submitData`, manual-vs-auto submit behavior → Task 4 Step 4
  (`applyDetectorEvent`). Game-mismatch skip with operator-visible message → Task 4 Step 4.
  Failure handling (unreachable at connect, drop mid-session, malformed message, restart
  auto-reconnect) → Task 1 Steps 6-9 (tests) and Task 3 Step 4 (startup auto-connect).
  Testing section's smoke test → Task 1; manual browser verification → Task 4 Steps 6-7.
- **Placeholder scan:** none found — every step has literal code or literal commands with
  expected output.
- **Type consistency:** `DetectorClient.status()` keys (`connected`, `host`, `port`, `error`)
  used consistently in Task 3's `_detector_status_payload` and Task 4's `renderDetectorStatus`.
  Event dict keys (`game`, `winner`, `confidence`, `ts`) consistent between Task 1's
  `_handle_message` and Task 4's `applyDetectorEvent`. `DETECTOR_GAME_MAP` keys match the
  detector's `Game` enum values (`sf6`, `tekken8`, `avatar`) confirmed against
  `~/repos/fgc-stream-event-detector/src/fgc_detector/types.py`.
