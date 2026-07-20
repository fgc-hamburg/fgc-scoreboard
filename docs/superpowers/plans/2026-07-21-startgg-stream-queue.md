# Start.gg Stream Queue Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let bracket runners load upcoming matches from a tournament's start.gg stream queue into the scoreboard control panel with one click, instead of typing names by hand.

**Architecture:** A new stdlib-only `startgg.py` module holds all domain logic (slug parsing, GraphQL fetch via `urllib`, response normalization, config persistence). `server.py` gains thin HTTP endpoints that call it and keep the bearer token server-side. `control.html` gains a "Stream Queue" section that renders match cards, auto-refreshes, and populates the form on click.

**Tech Stack:** Python 3 standard library only (`http.server`, `urllib.request`, `json`, `re`), vanilla JS in `control.html`, `unittest` for tests.

## Global Constraints

- **No new pip dependencies.** Standard library only (the `Makefile` runs `python3 server.py` on system python3). Do NOT import `requests` or `python-dotenv`.
- **The bearer token must never be sent to the browser.** All start.gg calls happen in `server.py`; endpoints must never serialize the token.
- **Feature is fully gated on `STARTGG_TOKEN`.** When it is absent/empty: config endpoint reports `enabled:false`, all other queue endpoints return `403`, and the UI renders nothing and starts no timer.
- **Slug rule:** use only the path segment immediately after `/tournament/` (e.g. `test-tournament-do-not-publish-1`); trim the rest.
- **Card click populates the form only — never auto-submits.** It clears Team / 2nd-player fields and resets scores to 0.
- Tests are stdlib `unittest`, run directly: `python3 tests/test_startgg.py -v`.

---

### Task 1: `startgg.py` — slug parsing

**Files:**
- Create: `startgg.py`
- Test: `tests/test_startgg.py`

**Interfaces:**
- Produces: `parse_slug(url: str | None) -> str | None` — returns the tournament slug, or `None` if it can't be parsed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_startgg.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from startgg import parse_slug


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_startgg.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'startgg'`

- [ ] **Step 3: Write minimal implementation**

Create `startgg.py`:

```python
"""Domain logic for the start.gg stream queue integration.

Standard-library only. All start.gg network access happens here so the bearer
token never reaches the browser.
"""

import re

_SLUG_RE = re.compile(r"/tournament/([^/?#]+)")


def parse_slug(url):
    """Extract the tournament slug from a start.gg URL or a bare slug.

    Returns the segment right after /tournament/ (e.g. "my-tournament"), or the
    input itself when it is already a bare slug, or None when unparseable.
    """
    if not url:
        return None
    url = url.strip()
    m = _SLUG_RE.search(url)
    if m:
        return m.group(1)
    if "/" not in url and "://" not in url:
        return url or None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add startgg.py tests/test_startgg.py
git commit -m "feat: add start.gg tournament slug parsing"
```

---

### Task 2: `startgg.py` — stream queue normalization

**Files:**
- Modify: `startgg.py`
- Test: `tests/test_startgg.py`

**Interfaces:**
- Consumes: the `data` object of a GraphQL response (`response["data"]`).
- Produces: `normalize_stream_queue(gql_data: dict | None) -> list[dict]` returning stations shaped `{"streamName": str, "sets": [{"fullRoundText": str|None, "round": int|None, "p1": str|None, "p2": str|None}]}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_startgg.py` (above the `if __name__` block), and add `normalize_stream_queue` to the import line:

```python
from startgg import parse_slug, normalize_stream_queue


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_startgg.py -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_stream_queue'`

- [ ] **Step 3: Write minimal implementation**

Append to `startgg.py`:

```python
def normalize_stream_queue(gql_data):
    """Flatten a GraphQL streamQueue response into a list of stations."""
    tournament = (gql_data or {}).get("tournament") or {}
    queue = tournament.get("streamQueue") or []
    stations = []
    for entry in queue:
        stream = entry.get("stream") or {}
        stream_name = stream.get("streamName") or "Stream"
        sets_out = []
        for s in entry.get("sets") or []:
            slots = s.get("slots") or []

            def name_at(i):
                if i < len(slots):
                    ent = slots[i].get("entrant")
                    if ent:
                        return ent.get("name")
                return None

            sets_out.append({
                "fullRoundText": s.get("fullRoundText"),
                "round": s.get("round"),
                "p1": name_at(0),
                "p2": name_at(1),
            })
        stations.append({"streamName": stream_name, "sets": sets_out})
    return stations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add startgg.py tests/test_startgg.py
git commit -m "feat: normalize start.gg stream queue responses"
```

---

### Task 3: `startgg.py` — GraphQL fetch and error handling

**Files:**
- Modify: `startgg.py`
- Test: `tests/test_startgg.py`

**Interfaces:**
- Produces:
  - `class StreamQueueError(Exception)`
  - `fetch_stream_queue(slug: str, token: str) -> list[dict]` — fetches from start.gg and returns normalized stations; raises `StreamQueueError` on network/HTTP/GraphQL failure.
  - Internal `_post_graphql(slug: str, token: str) -> dict` returning the response `data` object; raises `StreamQueueError` on failure. (Tested by patching `urllib.request.urlopen`.)
  - Module constants `API_URL` and `STREAM_QUEUE_QUERY`.

- [ ] **Step 1: Write the failing test**

Update the import line and append a test class to `tests/test_startgg.py`:

```python
from startgg import (
    parse_slug,
    normalize_stream_queue,
    fetch_stream_queue,
    StreamQueueError,
)
import io
import json as _json
import urllib.error
from unittest import mock


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_startgg.py -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_stream_queue'`

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `startgg.py` (next to `import re`):

```python
import json
import urllib.error
import urllib.request
```

Append to `startgg.py`:

```python
API_URL = "https://api.start.gg/gql/alpha"

STREAM_QUEUE_QUERY = """
query StreamQueueOnTournament($tourneySlug: String!) {
  tournament(slug: $tourneySlug) {
    streamQueue {
      stream { streamName }
      sets {
        fullRoundText
        round
        slots { entrant { name } }
      }
    }
  }
}
""".strip()


class StreamQueueError(Exception):
    """Raised when the start.gg stream queue cannot be fetched."""


def _post_graphql(slug, token):
    payload = json.dumps({
        "query": STREAM_QUEUE_QUERY,
        "variables": {"tourneySlug": slug},
    }).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise StreamQueueError(f"start.gg HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise StreamQueueError(f"start.gg unreachable: {e.reason}") from e
    try:
        data = json.loads(body)
    except ValueError as e:
        raise StreamQueueError("start.gg returned invalid JSON") from e
    if data.get("errors"):
        msg = data["errors"][0].get("message", "unknown error")
        raise StreamQueueError(f"start.gg error: {msg}")
    return data.get("data") or {}


def fetch_stream_queue(slug, token):
    """Fetch and normalize the stream queue for a tournament slug."""
    return normalize_stream_queue(_post_graphql(slug, token))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add startgg.py tests/test_startgg.py
git commit -m "feat: fetch start.gg stream queue over GraphQL"
```

---

### Task 4: `startgg.py` — config persistence and response builder

**Files:**
- Modify: `startgg.py`
- Test: `tests/test_startgg.py`

**Interfaces:**
- Produces:
  - `load_queue_config(path) -> dict` — returns `{"slug": ..., "streamName": ...}`, defaulting both to `None` when the file is missing or invalid.
  - `save_queue_config(path, cfg) -> None` — writes `slug` and `streamName` only.
  - `build_config_response(token: str, refresh_seconds: int, cfg: dict) -> dict` — returns `{"enabled": bool, "refreshSeconds": int, "slug": ..., "streamName": ...}`, never the token.

- [ ] **Step 1: Write the failing test**

Update the import line and append a test class to `tests/test_startgg.py`:

```python
from startgg import (
    parse_slug,
    normalize_stream_queue,
    fetch_stream_queue,
    StreamQueueError,
    load_queue_config,
    save_queue_config,
    build_config_response,
)
import tempfile


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_startgg.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_queue_config'`

- [ ] **Step 3: Write minimal implementation**

Append to `startgg.py`:

```python
DEFAULT_QUEUE_CONFIG = {"slug": None, "streamName": None}


def load_queue_config(path):
    """Read the persisted slug/streamName, defaulting to None on any problem."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return dict(DEFAULT_QUEUE_CONFIG)
    return {"slug": data.get("slug"), "streamName": data.get("streamName")}


def save_queue_config(path, cfg):
    out = {"slug": cfg.get("slug"), "streamName": cfg.get("streamName")}
    with open(path, "w") as f:
        json.dump(out, f, indent=2)


def build_config_response(token, refresh_seconds, cfg):
    """Build the /api/streamqueue/config payload. Never includes the token."""
    return {
        "enabled": bool(token),
        "refreshSeconds": refresh_seconds,
        "slug": cfg.get("slug"),
        "streamName": cfg.get("streamName"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add startgg.py tests/test_startgg.py
git commit -m "feat: persist stream queue config and build config response"
```

---

### Task 5: `server.py` — env loading and stream queue endpoints

**Files:**
- Modify: `server.py`

**Interfaces:**
- Consumes: everything from `startgg.py` (Tasks 1-4).
- Produces HTTP endpoints:
  - `GET /api/streamqueue/config` → config response (always available).
  - `GET /api/streamqueue` → `{stations, streamName}`; `403` if no token, `409` if no slug saved, `502` on start.gg failure.
  - `POST /api/streamqueue/tournament` `{url}` → `{slug, stations, streamName}`; `400` bad URL, `403`/`502` as above.
  - `POST /api/streamqueue/station` `{streamName}` → `{ok, streamName}`; `403` if no token.

- [ ] **Step 1: Add the `.env` loader and imports**

In `server.py`, add `import startgg` to the imports block (after `import re`).

Immediately after the line `BASE_DIR = Path(__file__).parent`, add:

```python


def _load_env_file(path):
    """Minimal .env loader (KEY=VALUE lines). Real env vars take precedence.

    Avoids a python-dotenv dependency so `python3 server.py` works on system
    python3 (see Makefile).
    """
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")
```

- [ ] **Step 2: Add module constants**

After the existing `PORT = int(os.environ.get("PORT", 8080))` line, add:

```python
QUEUE_CONFIG_PATH = BASE_DIR / "sc" / "streamqueue.json"
STARTGG_TOKEN = os.environ.get("STARTGG_TOKEN", "").strip()
STREAM_QUEUE_REFRESH_SECONDS = int(os.environ.get("STREAM_QUEUE_REFRESH_SECONDS", "30"))
```

- [ ] **Step 3: Add routes**

In `do_GET`, add these branches before the final `else`:

```python
        elif self.path == "/api/streamqueue/config":
            self._handle_streamqueue_config()
        elif self.path == "/api/streamqueue":
            self._handle_get_streamqueue()
```

In `do_POST`, add these branches before the final `else`:

```python
        elif self.path == "/api/streamqueue/tournament":
            self._handle_post_tournament()
        elif self.path == "/api/streamqueue/station":
            self._handle_post_station()
```

- [ ] **Step 4: Add the handler methods**

Add these methods to the `Handler` class (e.g. after `_handle_log_event`):

```python
    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _require_token(self):
        if not STARTGG_TOKEN:
            self.send_json(403, {"error": "Stream queue disabled (no STARTGG_TOKEN)"})
            return False
        return True

    def _handle_streamqueue_config(self):
        cfg = startgg.load_queue_config(QUEUE_CONFIG_PATH)
        self.send_json(200, startgg.build_config_response(
            STARTGG_TOKEN, STREAM_QUEUE_REFRESH_SECONDS, cfg))

    def _handle_get_streamqueue(self):
        if not self._require_token():
            return
        cfg = startgg.load_queue_config(QUEUE_CONFIG_PATH)
        if not cfg.get("slug"):
            self.send_json(409, {"error": "No tournament loaded"})
            return
        try:
            stations = startgg.fetch_stream_queue(cfg["slug"], STARTGG_TOKEN)
            self.send_json(200, {"stations": stations, "streamName": cfg.get("streamName")})
        except startgg.StreamQueueError as e:
            self.send_json(502, {"error": str(e)})

    def _handle_post_tournament(self):
        if not self._require_token():
            return
        try:
            body = self._read_json_body()
        except ValueError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        slug = startgg.parse_slug(body.get("url", ""))
        if not slug:
            self.send_json(400, {"error": "Could not parse tournament URL"})
            return
        startgg.save_queue_config(QUEUE_CONFIG_PATH, {"slug": slug, "streamName": None})
        try:
            stations = startgg.fetch_stream_queue(slug, STARTGG_TOKEN)
            self.send_json(200, {"slug": slug, "stations": stations, "streamName": None})
        except startgg.StreamQueueError as e:
            self.send_json(502, {"error": str(e)})

    def _handle_post_station(self):
        if not self._require_token():
            return
        try:
            body = self._read_json_body()
        except ValueError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        cfg = startgg.load_queue_config(QUEUE_CONFIG_PATH)
        cfg["streamName"] = body.get("streamName")
        startgg.save_queue_config(QUEUE_CONFIG_PATH, cfg)
        self.send_json(200, {"ok": True, "streamName": cfg["streamName"]})
```

- [ ] **Step 5: Smoke test — disabled path (no token)**

Ensure no `STARTGG_TOKEN` is set. Run in one shell:

```bash
STARTGG_TOKEN= python3 server.py &
sleep 1
curl -s localhost:8080/api/streamqueue/config
curl -s -o /dev/null -w "%{http_code}\n" localhost:8080/api/streamqueue
kill %1
```

Expected: config prints `{"enabled": false, "refreshSeconds": 30, "slug": null, "streamName": null}` and `/api/streamqueue` returns `403`.

- [ ] **Step 6: Smoke test — enabled gating (fake token, no slug)**

```bash
STARTGG_TOKEN=faketoken python3 server.py &
sleep 1
curl -s localhost:8080/api/streamqueue/config
curl -s -o /dev/null -w "%{http_code}\n" localhost:8080/api/streamqueue
kill %1
```

Expected: config shows `"enabled": true`; `/api/streamqueue` returns `409` (token present, no tournament loaded yet).

- [ ] **Step 7: Commit**

```bash
git add server.py
git commit -m "feat: add stream queue endpoints and .env loading to server"
```

---

### Task 6: `control.html` — Stream Queue UI

**Files:**
- Modify: `control.html`

**Interfaces:**
- Consumes: `/api/streamqueue/config`, `/api/streamqueue`, `/api/streamqueue/tournament`, `/api/streamqueue/station`.
- Reuses existing globals in the page script: `state`, `renderAll()`, `setDirty()`.

- [ ] **Step 1: Add the CSS**

In the `<style>` block, before the closing `</style>`, add:

```css
    /* Stream Queue */
    .queue-section {
      background: #252525;
      border: 1px solid #3a3a3a;
      border-radius: 8px;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .queue-header { display: flex; justify-content: space-between; align-items: center; }
    .queue-header h2 {
      font-size: 0.85rem; text-transform: uppercase;
      letter-spacing: 0.08em; color: #888;
    }
    .queue-controls { display: flex; gap: 0.6rem; align-items: center; }
    .queue-setup { display: flex; gap: 0.75rem; align-items: flex-end; }
    .queue-setup label { flex: 1; }
    .btn-load { background: #1e3a5f; color: #9dc3e6; padding: 0.5rem 0.9rem; white-space: nowrap; }
    .btn-load:hover { background: #254e7a; }
    .btn-refresh { background: #3a3a3a; color: #ccc; font-size: 0.8rem; }
    .btn-refresh:hover { background: #4a4a4a; }
    .queue-error { color: #d9534f; font-size: 0.82rem; }
    .queue-error:empty { display: none; }
    .queue-cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 0.75rem;
    }
    .match-card {
      background: #1a1a1a; border: 1px solid #3a3a3a; border-radius: 6px;
      padding: 0.75rem 0.9rem; cursor: pointer;
      display: flex; flex-direction: column; gap: 0.4rem;
      transition: border-color 0.15s, background 0.15s;
    }
    .match-card:hover { border-color: #c40a18; background: #202020; }
    .match-card .round-text {
      font-size: 0.72rem; text-transform: uppercase;
      letter-spacing: 0.06em; color: #888;
    }
    .match-card .players { font-size: 0.95rem; color: #e5e5e5; }
    .match-card .vs { color: #666; margin: 0 0.4rem; font-size: 0.8rem; }
    .queue-empty { color: #888; font-size: 0.85rem; }
```

- [ ] **Step 2: Add the HTML**

Inside `<div class="panel">`, immediately after the closing `</div>` of `<div class="actions">` (and before the panel's closing `</div>`), add:

```html
    <!-- Stream Queue -->
    <div class="queue-section" id="queueSection" style="display:none;">
      <div class="queue-header">
        <h2>Stream Queue</h2>
        <div class="queue-controls" id="queueControls" style="display:none;">
          <select id="queueStation"></select>
          <button class="btn-refresh" onclick="refreshQueue()">&#8635; Refresh</button>
        </div>
      </div>

      <div class="queue-setup" id="queueSetup">
        <label>
          start.gg Tournament URL
          <input type="text" id="tourneyUrl"
                 placeholder="https://www.start.gg/tournament/your-tournament/details">
        </label>
        <button class="btn-load" onclick="loadTournament()">Load tournament</button>
      </div>

      <div class="queue-error" id="queueError"></div>
      <div class="queue-cards" id="queueCards"></div>
    </div>
```

- [ ] **Step 3: Add the JS**

In the `<script>` block, immediately before the final `init();` call, add:

```javascript
    // ---------- Stream Queue ----------
    let queueConfig = { enabled: false, refreshSeconds: 30 };
    let queueStations = [];
    let queueTimer = null;

    async function initQueue() {
      try {
        const res = await fetch("/api/streamqueue/config");
        queueConfig = await res.json();
      } catch (_) { return; }
      if (!queueConfig.enabled) return;         // feature off — stay hidden

      document.getElementById("queueSection").style.display = "flex";
      if (queueConfig.slug) {
        document.getElementById("queueSetup").style.display = "none";
        await refreshQueue();
        startQueueTimer();
      }
    }

    function startQueueTimer() {
      if (queueTimer) clearInterval(queueTimer);
      const secs = queueConfig.refreshSeconds || 30;
      queueTimer = setInterval(refreshQueue, secs * 1000);
    }

    async function loadTournament() {
      const url = document.getElementById("tourneyUrl").value.trim();
      if (!url) return;
      setQueueError("");
      try {
        const res = await fetch("/api/streamqueue/tournament", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
        const data = await res.json();
        if (!res.ok) { setQueueError(data.error || "Failed to load tournament"); return; }
        queueConfig.slug = data.slug;
        queueConfig.streamName = data.streamName;
        queueStations = data.stations || [];
        document.getElementById("queueSetup").style.display = "none";
        renderStations();
        startQueueTimer();
      } catch (e) {
        setQueueError("Network error: " + e.message);
      }
    }

    async function refreshQueue() {
      try {
        const res = await fetch("/api/streamqueue");
        const data = await res.json();
        if (!res.ok) { setQueueError(data.error || "Failed to load queue"); return; }
        setQueueError("");
        queueConfig.streamName = data.streamName;
        queueStations = data.stations || [];
        renderStations();
      } catch (e) {
        setQueueError("Network error: " + e.message);
      }
    }

    function renderStations() {
      const controls = document.getElementById("queueControls");
      const sel = document.getElementById("queueStation");
      controls.style.display = queueStations.length ? "flex" : "none";

      const names = queueStations.map(s => s.streamName);
      const current = queueConfig.streamName && names.includes(queueConfig.streamName)
        ? queueConfig.streamName : (names[0] || null);
      sel.innerHTML = "";
      names.forEach(n => {
        const opt = document.createElement("option");
        opt.value = n; opt.textContent = n;
        if (n === current) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.style.display = names.length > 1 ? "" : "none";
      renderCards(current);
    }

    document.getElementById("queueStation").addEventListener("change", async (e) => {
      const streamName = e.target.value;
      queueConfig.streamName = streamName;
      renderCards(streamName);
      try {
        await fetch("/api/streamqueue/station", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ streamName }),
        });
      } catch (_) {}
    });

    function renderCards(streamName) {
      const wrap = document.getElementById("queueCards");
      wrap.innerHTML = "";
      const station = queueStations.find(s => s.streamName === streamName) || queueStations[0];
      const sets = station ? station.sets : [];
      if (!sets.length) {
        const empty = document.createElement("div");
        empty.className = "queue-empty";
        empty.textContent = "No matches queued.";
        wrap.appendChild(empty);
        return;
      }
      sets.forEach(s => {
        const card = document.createElement("div");
        card.className = "match-card";
        const p1 = s.p1 || "TBD";
        const p2 = s.p2 || "TBD";
        card.innerHTML =
          `<div class="round-text">${escapeHtml(s.fullRoundText || "")}</div>` +
          `<div class="players">${escapeHtml(p1)}<span class="vs">vs</span>${escapeHtml(p2)}</div>`;
        card.addEventListener("click", () => loadMatch(s));
        wrap.appendChild(card);
      });
    }

    function loadMatch(set) {
      state.p1Name = set.p1 || "";
      state.p2Name = set.p2 || "";
      state.round  = set.fullRoundText || "";
      state.p1Score = 0;
      state.p2Score = 0;
      // Clear team & 2nd-player fields — the queue doesn't provide them
      state.p1Team = ""; state.p1Name2 = ""; state.p1Team2 = "";
      state.p2Team = ""; state.p2Name2 = ""; state.p2Team2 = "";
      renderAll();
      setDirty(true);
    }

    function setQueueError(msg) {
      document.getElementById("queueError").textContent = msg || "";
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c]));
    }
```

- [ ] **Step 4: Call `initQueue()` from `init()`**

Change the `init()` function body to append the queue init (do not await — it runs independently):

```javascript
    async function init() {
      await loadRounds();
      await loadData(true);
      setInterval(() => loadData(false), 2000);
      initQueue();
    }
```

- [ ] **Step 5: Manual verification — feature hidden without token**

```bash
STARTGG_TOKEN= python3 server.py
```

Open `http://localhost:8080`. Confirm: **no Stream Queue section appears**, and the rest of the panel works as before. Stop the server.

- [ ] **Step 6: Commit**

```bash
git add control.html
git commit -m "feat: add stream queue UI to control panel"
```

---

### Task 7: Config files and documentation

**Files:**
- Modify: `.env`
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Add env vars to `.env`**

Append to `.env`:

```
# start.gg stream queue (optional). Without a token the feature stays hidden.
STARTGG_TOKEN=
STREAM_QUEUE_REFRESH_SECONDS=30
```

- [ ] **Step 2: Ignore the persisted queue state**

Append to `.gitignore`:

```
sc/streamqueue.json
```

- [ ] **Step 3: Document the feature in `README.md`**

Add this section near the control dashboard docs in `README.md`:

```markdown
### Start.gg Stream Queue

The control dashboard can pull upcoming matches from a tournament's start.gg
stream queue so you don't have to type player names by hand.

Set these in your `.env`:

- `STARTGG_TOKEN` — a start.gg API token (bearer). **If this is empty the
  stream-queue section is hidden entirely.** The token is only ever used
  server-side and is never sent to the browser.
- `STREAM_QUEUE_REFRESH_SECONDS` — how often the queue auto-refreshes
  (default `30`).

In the dashboard, paste your tournament URL
(e.g. `https://www.start.gg/tournament/your-tournament/details`) and click
**Load tournament**. Upcoming matches appear as cards; pick a stream station if
your event has more than one. Clicking a card fills in the round and player
names and resets the score to 0:0 — review it, then press **Submit**. The queue
also refreshes on its own and via the **Refresh** button.
```

- [ ] **Step 4: Verify tests still pass and commit**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS (19 tests)

```bash
git add .env .gitignore README.md
git commit -m "docs: document start.gg stream queue config and usage"
```

Note: `.env` is gitignored, so `git add .env` will report nothing — that is expected. Update the local `.env` regardless so the running server picks up the new variables.

---

## Notes for the implementer

- Run the full test file after each Python task: `python3 tests/test_startgg.py -v`.
- To exercise the enabled path end-to-end you need a real `STARTGG_TOKEN` and a
  real tournament slug; the automated smoke tests in Task 5 only cover the
  disabled and no-slug gates, which need no token.
