# Match-Detector Event Consumer — Design

**Date:** 2026-08-01
**Status:** Approved, ready for implementation planning
**Scope:** Consume `match_end` events from the `fgc-stream-event-detector` WebSocket server
(a separate repo, already built) inside the FGC Scoreboard control dashboard, and use them to
update the loaded match's score.

---

## Problem

The detector (`~/repos/fgc-stream-event-detector`) already runs its own WebSocket *server* and
emits typed JSON events, including `match_end` (winner + game + confidence + timestamp) — see
its `src/fgc_detector/events.py` and `src/fgc_detector/server.py`. Nothing in fgc-scoreboard
consumes it yet. The dashboard (`server.py` + `control.html`) is a synchronous stdlib
`HTTPServer` with no WebSocket client today.

This design covers only the consumer side: connecting to the detector, receiving `match_end`,
and deciding what the dashboard does with it. The detector itself, its detection logic, and its
wire protocol are out of scope and already implemented in the other repo.

## Non-goals

- Any change to the detector repo.
- Round-level or set-level logic beyond "+1 to the winner's score". Set format (FT2/FT3) stays
  operator-driven, same as today.
- A confidence threshold in the dashboard. The detector already performs N-frame agreement
  before firing `match_end`; the dashboard trusts it.
- Player identity beyond `p1`/`p2` screen side — same limitation the detector has.
- Multiple simultaneous dashboard tabs/consumers. Single-operator, single-tab usage, same
  assumption `streamcontrol.json` already makes as a single-writer file.

---

## Architecture

```
detector (ws server)  <──ws client──  DetectorClient (new module, background thread)
                                              │
                                  in-memory pending-events list (lock-protected)
                                              │
control.html  ──poll GET /api/detector/events──  server.py (existing sync HTTPServer)
```

### `detector_client.py` (new module)

A `DetectorClient` class that owns its own asyncio event loop, run in a daemon thread — kept
fully separate from `server.py`'s synchronous request-handling thread.

- `connect(host, port)` — (re)starts the background loop, connects to `ws://{host}:{port}`
  (the detector's root path takes all traffic; no sub-path).
- `disconnect()` — stops the loop and any pending reconnect attempts. Explicit, user-initiated.
- `status()` — thread-safe snapshot: `{connected: bool, host, port, error: str | None}`.
- `drain_events()` — thread-safe: pops and returns all pending `match_end` events collected
  since the last drain, as plain dicts `{game, winner, confidence, ts}`.
- On unexpected disconnect (not a user-initiated `disconnect()`), retries with exponential
  backoff (1s, 2s, 4s... capped at 30s) until it reconnects or is explicitly stopped.
- Parses inbound JSON per message `type`: `match_end` appends to the pending list; `status` and
  `config` update the connection snapshot's last-known fields (not surfaced further in v1);
  anything unrecognized is logged and ignored — the detector's own `events.py` is the schema
  authority, this module does not re-validate beyond checking `type`.

This is fgc-scoreboard's first non-stdlib dependency (needs a WebSocket client library — the
`websockets` package matches what the detector itself uses). Kept scoped to this one module;
`server.py`'s HTTP handling stays stdlib-only.

### Persistence: `sc/detectorconfig.json`

```json
{"host": "localhost", "port": 8765, "autoSubmit": false}
```

Same pattern as `sc/streamqueue.json`. `host`/`port` are `null` when disconnected. On server
startup, if a host/port is saved, `server.py` calls `connect()` automatically so a restart
doesn't require the operator to re-enter connection details.

### New HTTP endpoints (`server.py`)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/detector/status` | — | `{connected, host, port, error, autoSubmit}` |
| POST | `/api/detector/connect` | `{host, port}` | same as status, after attempting connect |
| POST | `/api/detector/disconnect` | — | same as status |
| POST | `/api/detector/auto_submit` | `{enabled}` | same as status |
| GET | `/api/detector/events` | — | `{events: [...]}`, **draining** the pending list |

`/api/detector/events` has mailbox semantics: each GET returns and clears whatever has
accumulated since the last GET. This matches the single-tab assumption above; a second consumer
would silently steal events, which is an accepted limitation, not a bug to design around.

### Game-code mapping (client-side, in `control.html`)

```js
{sf6: "SF6", tekken8: "TEKKEN8", avatar: "AVATAR"}
```

Adds a new `AVATAR` `<option>` to the existing Game `<select>` (currently: 2XKO, BBCF, BBTAG,
COTW, DBFZ, GGST, GGXRD, KOFXIV, MVCI, SF6, SFVCE, TEKKEN7, TEKKEN8, UMVC3, UNICLR, USF4) —
there is currently no dropdown value for the detector's `avatar` (Avatar Legends of the Arena).

### `control.html` changes

**New "Match Detector" panel**, styled like the existing Stream Queue section:
- Host / port text inputs + Connect / Disconnect button.
- Connection-status dot (connected / disconnected) + error text, mirroring the dirty-dot pattern
  already used for form state.
- "Auto-submit" checkbox.

**New poll loop**, alongside the existing 2s `loadData` interval, hits
`GET /api/detector/events`. For each event returned, in order:

1. Map `event.game` through the table above to the dropdown's game code. If it doesn't equal the
   currently-loaded `state.game`, **skip** the event and show a transient status message (reusing
   `showStatus`), e.g. `"Ignored P1 win — event was for avatar, loaded game is SF6"`.
2. Otherwise, resolve `winner: "p1" | "p2"` to player number 1 or 2 and call the **existing**
   `adjustScore(player, 1)` unmodified — this is deliberately "as if the operator clicked +1":
   it mutates `state`, flips the dirty flag, updates the score display, and logs a
   `score_change` event through the existing `logEvent` call.
3. If the auto-submit checkbox is on, call the **existing** `submitData()` right after — same
   as if the operator then clicked Submit. In manual mode, the event just sits dirty until the
   operator reviews and clicks Submit themselves, same as any other manual edit today.

No new score-mutation code path is introduced; the detector event reuses the same functions a
mouse click would.

---

## Failure handling

- **Detector unreachable at connect time** — `connect()`'s underlying attempt fails, `status.error`
  is set, `connected` stays `false`. The dashboard shows the error text next to the status dot.
  No retry loop starts until a *successful* connect is later followed by an unexpected drop.
- **Detector connection drops mid-session** — background reconnect with backoff, `connected`
  flips to `false` in the meantime so the dashboard reflects it on the next status poll.
- **Malformed/unrecognized message from the detector** — logged, ignored, connection stays up.
- **Game mismatch** — event is dropped after a transient operator-visible message, never applied.
- **Scoreboard HTTP server restart while detector connection is live** — on restart, saved
  `host`/`port` in `sc/detectorconfig.json` triggers auto-reconnect; any events in-flight during
  the restart window are lost (acceptable — same as any other in-memory state in this process).

---

## Testing

fgc-scoreboard currently has no pytest scaffold (`tests/test_startgg.py` exists but there's no
shared runner config beyond that). This adds:

- A script-level smoke test for `detector_client.py`: spin up a throwaway `websockets` server in
  the test, drive `DetectorClient.connect()` against it, send a `match_end` message, assert
  `drain_events()` returns it and a second `drain_events()` call is empty. Also test the
  reconnect-after-drop path and the "connect fails immediately" path.
- Manual verification of the `control.html` flow against either a locally running detector or a
  small stub script that opens a `websockets` server and sends canned `match_end` messages on a
  timer — used to confirm the game-mismatch skip, the dirty-flag/manual-submit behavior, and the
  auto-submit behavior end-to-end in a browser.

---

## Stack change

Adds `websockets` as fgc-scoreboard's first non-stdlib Python dependency, used only by
`detector_client.py`. `server.py`'s HTTP handling and everything else in the repo remains
stdlib-only.
