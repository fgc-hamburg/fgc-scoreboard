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
        self._task = None
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
            self._task = None
        thread.start()

    def disconnect(self):
        with self._lock:
            self._generation += 1
            self._host = None
            self._port = None
            self._connected = False
            self._error = None
            loop = self._loop
            thread = self._thread
            task = self._task
            self._loop = None
            self._thread = None
            self._task = None
        if loop is not None and loop.is_running() and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        if thread is not None:
            thread.join(timeout=5)

    def _run_loop(self, loop, host, port, generation):
        asyncio.set_event_loop(loop)
        task = loop.create_task(self._connect_loop(host, port, generation))
        with self._lock:
            if generation == self._generation:
                self._task = task
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()

    def _is_current(self, generation):
        with self._lock:
            return generation == self._generation

    async def _connect_loop(self, host, port, generation):
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
