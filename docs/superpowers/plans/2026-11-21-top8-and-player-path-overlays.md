# Top 8 Bracket & Player Path Overlays Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two new OBS overlays: a live top 8 bracket and a selected player's path to the current match. Both are fed from start.gg by the dashboard server.

**Architecture:** The dashboard sends the active set (from the stream queue) on Submit. `server.py` persists it and runs a background refresh thread. Pure functions in a new `overlays.py` turn start.gg set lists into `sc/top8.json` / `sc/playerpath.json`, and the GraphQL fetchers live in `startgg.py`. The overlay pages are static files that poll those JSON files, like `scoreboard.js` does.

**Tech Stack:** Python 3 stdlib (`http.server`, `threading`, `urllib`, `unittest`), plain HTML/CSS/JS (no build step).

**Spec:** `docs/superpowers/specs/2026-11-21-top8-and-player-path-overlays-design.md`

## Global Constraints

- Server code is Python standard library only. No new dependencies in `startgg.py` / `overlays.py`.
- `STARTGG_TOKEN` must never be written to any file under `sc/` or sent to the browser.
- On any start.gg error, existing `sc/top8.json` / `sc/playerpath.json` are left untouched.
- JSON files are written atomically (temp file in the same dir + `os.replace`).
- The refresh interval reuses `STREAM_QUEUE_REFRESH_SECONDS` (default 30).
- Overlays must work opened via `file://` in OBS: 1920×1080 body, transparent-free dark background like `_overlays/setup.html`, `Valorant` font from `fonts/ValorantFont.ttf`, uppercase, accents pink `#e91e8c`, cyan `#00e5ff`, orange `#ffa500`, logo `imgs/fgc_hamburg_transp.png`.
- Overlays poll their JSON every 500 ms with a `?v=<counter>` cache-buster.
- All text from start.gg inserted into overlay HTML is escaped (use `textContent`, never `innerHTML` with data).
- Run tests with `.venv/bin/python -m unittest discover tests`. System python lacks `websockets`, so `test_detector_client` fails to import there.

## Review Focus

1. **Bracket with fewer than 8 entrants, or missing losers rounds:** every round key still exists in `top8.json`, and missing matches are empty placeholder matches. No exception. Test: `test_top8_small_bracket_pads_missing_rounds` (Task 1).
2. **GF reset set exists but was never played (slots empty or set not started):** `grandFinalReset` is `[]`. Test: `test_top8_unplayed_reset_hidden` (Task 1).
3. **start.gg error mid-refresh** (e.g. 2nd page of sets fails): neither JSON file is changed. Test: `test_refresh_error_leaves_files_untouched` (Task 3).
4. **Manual match / post-report Submit** (`setId: null`): the server keeps the previous `setId` and the files. Test: `test_merge_active_state_keeps_previous_when_set_null` (Task 3).
5. **DQ in a set** (score `-1`): it shows as `"DQ"` in both outputs, not `-1`. Tests: `test_top8_dq_score` (Task 1), `test_path_dq` (Task 2).

---

### Task 1: Top 8 builder (pure)

**Files:**
- Create: `overlays.py`
- Create: `tests/test_overlays.py`

**Interfaces:**
- Consumes: `startgg._slot(slots, i) -> (entrant_id, name, team)` (existing).
- Produces:
  - `overlays.ROUND_KEYS = ["winnersSemis","winnersFinal","grandFinal","grandFinalReset","losersTop8","losersQuarters","losersSemis","losersFinal"]`
  - `overlays.build_top8(sets: list[dict], tournament_name: str, event_name: str, now: int) -> dict`. `sets` are raw start.gg set nodes with fields `id, round, fullRoundText, identifier, state, winnerId, slots { entrant { id name participants { prefix gamerTag } } standing { stats { score { value } } } }`.
  - Output shape exactly as in spec §5: `{updatedAt, tournamentName, eventName, rounds: {<key>: [Match]}}`, `Match = {setId, state, p1: Slot, p2: Slot}`, `Slot = {name, team, score, winner}`.

Round classification (spec §4):
- GF reset = sets whose `fullRoundText == "Grand Final Reset"`. Keep a reset only if both slots have an entrant. Otherwise `grandFinalReset = []`.
- `W` = max `round` among positive-round sets that are not GF reset. `L` = min `round` among negative-round sets.
- winnersSemis=`W-2`, winnersFinal=`W-1`, grandFinal=`W`, losersTop8=`L+3`, losersQuarters=`L+2`, losersSemis=`L+1`, losersFinal=`L`.
- Within a round, sort by `identifier` using key `(len(identifier), identifier)`.
- Pad each round to its expected count (2,1,1,—,2,2,1,1) with an empty match `{setId: None, state: None, p1: EMPTY, p2: EMPTY}` where `EMPTY = {name: None, team: "", score: None, winner: False}`. Truncate if a round somehow has more.
- Slot score: `standing.stats.score.value`. `None` → `None`, `-1` → `"DQ"`, else `int`. Only populate scores when `state == 3` (completed). Otherwise `None`. `winner = entrant.id == winnerId` (False if winnerId is None).
- No losers sets at all → all losers keys padded empty.

- [ ] **Step 1: Write failing tests** in `tests/test_overlays.py` (use the `sys.path` insert pattern from `tests/test_startgg.py`). Build fixtures with a small helper `mkset(id, round, text, ident, p1=(eid,tag,prefix,score)|None, p2=..., winner=None, state=3)`. Tests:
  - `test_top8_classifies_full_bracket`: an 8+-person bracket with winners rounds 1..5 (5 = "Grand Final"), losers -1..-6. Assert winnersSemis are the round-3 sets sorted by identifier, winnersFinal is round 4, grandFinal is round 5, losersTop8 is round -3, losersQuarters is -4, losersSemis is -5, losersFinal is -6, and lower rounds are absent.
  - `test_top8_slot_fields`: completed set p1 tag "Daigo" prefix "EG" score 2, p2 score 1, winnerId=p1 → `p1 == {"name":"Daigo","team":"EG","score":2,"winner":True}`, `p2["winner"] is False`.
  - `test_top8_unreported_scores_none`: `state=1` with scores in standing → both scores `None`.
  - `test_top8_dq_score`: completed, p2 score -1 → `p2["score"] == "DQ"`.
  - `test_top8_reset_detected`: an extra set round 6 "Grand Final Reset" with both entrants → `grandFinalReset` has 1 match, and `grandFinal` is still the round-5 set.
  - `test_top8_unplayed_reset_hidden`: reset set with empty slots → `grandFinalReset == []`.
  - `test_top8_small_bracket_pads_missing_rounds`: only rounds 1..3 winners (3=GF) and -1..-2 losers → every `ROUND_KEYS` key present, `len(rounds["winnersSemis"]) == 2`, `len(rounds["losersTop8"]) == 2`, padded entries have `setId is None`.
  - `test_top8_header_fields`: `tournamentName`, `eventName`, `updatedAt == now` pass through.
- [ ] **Step 2: Run** `.venv/bin/python -m unittest tests.test_overlays -v`. Expected: FAIL (ImportError: no module `overlays`).
- [ ] **Step 3: Implement** `build_top8` and the private helpers `_score(slot_dict, completed) -> int|str|None` and `_match(set_node) -> dict` in `overlays.py` (module docstring: "Pure builders for the top 8 / player path overlay JSON.").
- [ ] **Step 4: Run** the same command. Expected: all PASS.
- [ ] **Step 5: Commit** `git add overlays.py tests/test_overlays.py && git commit -m "feat: build top 8 bracket data from start.gg sets"`

### Task 2: Player path builder (pure)

**Files:**
- Modify: `overlays.py`
- Modify: `tests/test_overlays.py`

**Interfaces:**
- Consumes: `_score`, `startgg._slot` from Task 1.
- Produces: `overlays.build_player_path(sets: list[dict], entrant_id: int, active_set: dict, event_name: str, now: int) -> dict`. `sets` are the entrant's event set nodes (fields `id, fullRoundText, state, winnerId, completedAt, slots{...as Task 1}`). `active_set` is the raw node of the current set (fields as in Task 1). Output is exactly spec §5 `playerpath.json`: `{updatedAt, eventName, player: {name, team}, currentRound, sets: [{round, opponent: {name, team}, score, won}]}`.

Rules:
- `player` is taken from the `active_set` slot whose entrant id == `entrant_id`. `currentRound = active_set["fullRoundText"]`.
- Include a set only if `state == 3`, `id != active_set["id"]`, and both slots have entrants (skips byes).
- Sort by `completedAt` ascending (`None` last).
- `score` is `f"{mine}-{theirs}"` from the player's perspective. If either score is -1, it's `"DQ"`. If both are None, it's `""`.
- `won = winnerId == entrant_id`.
- Compare ids with `str(a) == str(b)` (start.gg sometimes returns ids as strings). Apply the same rule in Task 1's `winner`.

- [ ] **Step 1: Write failing tests:**
  - `test_path_orders_chronologically_and_excludes_active`: 3 completed sets with completedAt 300, 100, 200 plus the active set. Result rounds are in 100, 200, 300 order, and the active set id is absent.
  - `test_path_score_from_player_perspective`: player in slot 2 winning 2-1 → `score == "2-1"`, `won is True`, opponent is slot 1's `{name, team}`.
  - `test_path_includes_losses`: player lost 0-2 → `score == "0-2"`, `won is False`.
  - `test_path_skips_incomplete_and_byes`: `state=2` set and set with one empty slot → both excluded.
  - `test_path_dq`: opponent score -1 → `score == "DQ"`, `won is True`.
  - `test_path_header`: `player`, `currentRound`, `eventName`, `updatedAt` are correct, and string vs int entrant id still matches.
- [ ] **Step 2: Run** `.venv/bin/python -m unittest tests.test_overlays -v`. Expected: new tests FAIL with AttributeError.
- [ ] **Step 3: Implement** `build_player_path` in `overlays.py`.
- [ ] **Step 4: Run** the same command. Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: build player path data from start.gg sets"` (after `git add` of both files).

### Task 3: start.gg fetchers, overlay state, refresh orchestration

**Files:**
- Modify: `startgg.py` (append queries + fetchers)
- Modify: `overlays.py` (state persistence, atomic write, `refresh`)
- Modify: `tests/test_overlays.py`, `tests/test_startgg.py`
- Modify: `.gitignore` (add `sc/overlaystate.json`, `sc/top8.json`, `sc/playerpath.json`)

**Interfaces:**
- Consumes: `startgg._graphql(query, variables, token)`, `StreamQueueError`, Task 1/2 builders.
- Produces (in `startgg.py`):
  - `fetch_set_context(set_id, token) -> dict` returns the raw `set` node with `id round fullRoundText identifier state winnerId slots{...} event { id name tournament { name } } phaseGroup { id }`. Raises `StreamQueueError("Set not found")` if null.
  - `fetch_phase_group_sets(phase_group_id, token, per_page=40) -> list[dict]` paginates `phaseGroup(id){ sets(page:$page, perPage:$perPage, sortType: STANDARD){ pageInfo{ totalPages } nodes{ <Task 1 fields> } } }` until `page >= totalPages`.
  - `fetch_entrant_event_sets(event_id, entrant_id, token, per_page=40) -> list[dict]` paginates `event(id){ sets(page, perPage, filters:{ entrantIds:[$entrantId], state:[3] }){ pageInfo{ totalPages } nodes{ id fullRoundText state winnerId completedAt slots{...} } } }`.
  - Shared slot selection string: `slots { entrant { id name participants { prefix gamerTag } } standing { stats { score { value } } } }`.
- Produces (in `overlays.py`):
  - `DEFAULT_STATE = {"setId": None, "p1Id": None, "p2Id": None, "pathSide": "p1"}`
  - `load_state(path) -> dict` (defaults on missing/invalid file) and `save_state(path, state) -> None`.
  - `merge_active_state(prev: dict, body: dict) -> dict`: if `body["setId"]` is falsy, return `prev` with only `pathSide` updated. Otherwise return the new `setId/p1Id/p2Id/pathSide`. `pathSide` not in `("p1","p2")` → `"p1"`.
  - `write_json_atomic(path: Path, data: dict) -> None`
  - `refresh(state: dict, token: str, sc_dir: Path, now: int, context_cache: dict) -> None`. If no `setId`, return. Otherwise: context = `context_cache.get(setId)`, or fetch it and store it as `context_cache = {setId: ctx}` (clear other keys). Then fetch phase-group sets and the entrant's event sets (entrant = `p1Id` or `p2Id` per `pathSide`). Build **both** dicts first, then write both files. Any `StreamQueueError` propagates before any write. Tournament name = `ctx["event"]["tournament"]["name"]`, event name = `ctx["event"]["name"]`, active set for path = `ctx`.

- [ ] **Step 1: Write failing tests** (mock `startgg._graphql` with `mock.patch`, as existing tests do):
  - `tests/test_startgg.py::test_fetch_phase_group_sets_paginates`: the mock returns totalPages=2 → 2 calls, and the nodes are concatenated in order. Assert the second call's `variables["page"] == 2`.
  - `tests/test_startgg.py::test_fetch_entrant_event_sets_passes_filter`: assert `variables["entrantId"] == 42` and `variables["eventId"] == 7`.
  - `tests/test_startgg.py::test_fetch_set_context_missing_raises`: `{"set": None}` → `StreamQueueError`.
  - `tests/test_overlays.py::test_merge_active_state_keeps_previous_when_set_null`: prev has setId 5. Body `{setId: None, pathSide: "p2"}` → setId 5, pathSide "p2".
  - `test_merge_active_state_replaces_and_sanitizes_side`: body setId 9, pathSide "x" → setId 9, pathSide "p1".
  - `test_state_roundtrip_and_defaults`: missing file → `DEFAULT_STATE`, and save then load round-trips.
  - `test_refresh_writes_both_files`: patch `startgg.fetch_set_context`, `fetch_phase_group_sets`, and `fetch_entrant_event_sets` with fixture data in a `tempfile.TemporaryDirectory()`. Both files exist, `top8.json["eventName"]` is correct, and `fetch_entrant_event_sets` was called with the `p2Id` when `pathSide == "p2"`.
  - `test_refresh_caches_context`: two refreshes with the same setId → `fetch_set_context` called once.
  - `test_refresh_error_leaves_files_untouched`: pre-write both files with `{"old": true}`, make `fetch_entrant_event_sets` raise `StreamQueueError`. `refresh` raises, and both files still contain `{"old": true}`.
  - `test_refresh_noop_without_set`: state with `setId None` → no fetch calls, no files.
- [ ] **Step 2: Run** `.venv/bin/python -m unittest discover tests -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the fetchers in `startgg.py` and the state/refresh functions in `overlays.py`. Update `.gitignore`.
- [ ] **Step 4: Run** `.venv/bin/python -m unittest discover tests -v`. Expected: all PASS.
- [ ] **Step 5: Live check (only if `.env` has `STARTGG_TOKEN`)**: with a known completed tournament set id, run `.venv/bin/python -c "import startgg,os,json;..."` to call `fetch_set_context` and `fetch_phase_group_sets` and print each set's `round`, `fullRoundText` and `identifier`. Confirm that the GF reset uses `fullRoundText == "Grand Final Reset"`. If it doesn't, adjust the Task 1 classification and its test, and record what was observed in the commit message. If there's no token, note "GF reset unverified against live API" in the report.
- [ ] **Step 6: Commit** `git commit -m "feat: fetch overlay data from start.gg and write overlay JSON"` (add all touched files).

### Task 4: Server endpoint + refresh thread

**Files:**
- Modify: `server.py`

**Interfaces:**
- Consumes: `overlays.load_state/save_state/merge_active_state/refresh/DEFAULT_STATE`, `startgg.StreamQueueError`.
- Produces: `POST /api/overlays/active` (body `{setId, p1Id, p2Id, pathSide}`) → 200 `{"ok": true, "state": <merged>}`, or 403 without a token (via `_require_token`), or 400 on invalid JSON. Also `GET /api/overlays/status` → `{"lastRefresh": int|None, "lastError": str|None}`.

Implementation decisions:
- Add `OVERLAY_STATE_PATH = BASE_DIR / "sc" / "overlaystate.json"` and `SC_DIR = BASE_DIR / "sc"`.
- Add a module-level `OVERLAY_WAKE = threading.Event()`, `OVERLAY_STATUS = {"lastRefresh": None, "lastError": None}`, and `_overlay_ctx_cache = {}`.
- `overlay_loop()`: `while True:` load the state, then `overlays.refresh(state, STARTGG_TOKEN, SC_DIR, int(time.time()), _overlay_ctx_cache)`. On success set `lastRefresh` and clear `lastError`. On `StreamQueueError` (or any `Exception`, since the thread must never die) store `str(e)` in `lastError` and print it. Then `OVERLAY_WAKE.wait(STREAM_QUEUE_REFRESH_SECONDS); OVERLAY_WAKE.clear()`.
- Start it as a daemon thread in `__main__` only when `STARTGG_TOKEN` is set.
- `/api/overlays/active`: merge, save, `OVERLAY_WAKE.set()`.
- `_handle_post_report`: on success, `OVERLAY_WAKE.set()`.
- `_handle_post_clear`: also `overlays.save_state(OVERLAY_STATE_PATH, dict(overlays.DEFAULT_STATE))`.
- Switch `HTTPServer` to `ThreadingHTTPServer` so the slow start.gg calls don't block the dashboard (the stdlib has it).

- [ ] **Step 1: Implement** the above.
- [ ] **Step 2: Verify the server boots and the endpoint works:** run `PORT=8099 STARTGG_TOKEN=dummy .venv/bin/python server.py` in the background, then `curl -s -XPOST localhost:8099/api/overlays/active -d '{"setId":null,"pathSide":"p2"}'`. Expected: `{"ok": true, "state": {... "pathSide": "p2"}}`. Then `curl -s localhost:8099/api/overlays/status` should return JSON with `lastRefresh`/`lastError` keys. Kill the server and delete `sc/overlaystate.json` if it was created.
- [ ] **Step 3: Run** `.venv/bin/python -m unittest discover tests`. Expected: OK.
- [ ] **Step 4: Commit** `git commit -am "feat: refresh overlay JSON from start.gg in the dashboard server"`

### Task 5: Dashboard P1/P2 toggle and send-on-Submit

**Files:**
- Modify: `control.html`

**Interfaces:**
- Consumes: `POST /api/overlays/active` (Task 4). Existing `state`, `activeSet`, `queueConfig`, `setDirty`, `submitData`, `swapPlayers`.
- Produces: the UI only.

Decisions:
- Add `state.pathSide = "p1"` to the initial state.
- In `#queueSection`, inside `.queue-header-actions` before the Change-tournament button, add a labeled segmented toggle "Player path: [P1] [P2]" (two buttons, `id="pathP1"`/`id="pathP2"`, active one styled with the existing accent). Clicking sets `state.pathSide`, re-renders the toggle, and calls `setDirty(true)`.
- `swapPlayers()` already swaps `activeSet` ids. Leave `pathSide` as is: it refers to the side, and the IDs follow the players.
- In `submitData()`, after a successful `/api/update` and only if `queueConfig.enabled`, POST `/api/overlays/active` with `{setId: activeSet?.setId ?? null, p1Id: activeSet?.p1Id ?? null, p2Id: activeSet?.p2Id ?? null, pathSide: state.pathSide}`. Ignore failures except via `setQueueError`.

- [ ] **Step 1: Implement** the above.
- [ ] **Step 2: Verify:** run the server as in Task 4 Step 2 (with `STARTGG_TOKEN=dummy`), open `http://localhost:8099` with headless Chrome to confirm the page renders without JS errors. Use `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu --enable-logging=stderr --v=0 --screenshot=/tmp/control.png --window-size=1400,1200 http://localhost:8099` and check stderr for `Uncaught`. Then simulate Submit with `curl` against `/api/overlays/active` to confirm the server side still works. Report what was visually verified.
- [ ] **Step 3: Commit** `git commit -am "feat: player path side toggle and overlay state sync on submit"`

### Task 6: Top 8 overlay page

**Files:**
- Create: `_overlays/top8.html`
- Create: `tests/fixtures/top8.sample.json` (an 8-player example with mixed reported/unreported, one DQ, and a populated GF reset)

**Interfaces:**
- Consumes: `../sc/top8.json` in the Task 1 shape.

Decisions:
- Self-contained page (inline `<style>` + `<script>`, no jQuery/GSAP) following the layout sketch in spec §6. Header bar at the top: logo left, then `<eventName>` + " TOP 8" (with "TOP 8" in pink), and the tournament name as a smaller cyan subtitle. Winners block in the upper half with columns WINNERS SEMIS | WINNERS FINAL | GRAND FINAL | GF RESET (the reset column is hidden when the array is empty). Losers block in the lower half with columns LOSERS TOP 8 | LOSERS QUARTERS | LOSERS SEMIS | LOSERS FINAL. Bracket connector lines are thin, semi-transparent white.
- Match box = 2 rows. Each row: team (orange, smaller) + name (white), and a score cell on the right (pink background). Winner row full opacity, loser row 0.45 opacity, TBD row shows "TBD" dimmed.
- The script polls `../sc/top8.json?v=N` every 500 ms. It re-renders only when the response text changes. A missing file or parse error keeps the current DOM, and before the first success it shows the header frame only.
- Add an optional query param `?src=` to override the JSON path (used for verification with the fixture).

- [ ] **Step 1: Write the fixture and the page.**
- [ ] **Step 2: Verify visually:** `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu --allow-file-access-from-files --screenshot=/tmp/top8.png --window-size=1920,1080 --virtual-time-budget=3000 "file://$PWD/_overlays/top8.html?src=../tests/fixtures/top8.sample.json"`. Look at `/tmp/top8.png` and confirm all 8 rounds are placed per the sketch, scores show, DQ shows "DQ", and the reset column is visible. Then edit a copy of the fixture with an empty `grandFinalReset` and confirm the column is hidden.
- [ ] **Step 3: Commit** `git add _overlays/top8.html tests/fixtures/top8.sample.json && git commit -m "feat: top 8 bracket overlay"`

### Task 7: Player path overlay page + README

**Files:**
- Create: `_overlays/playerpath.html`
- Create: `tests/fixtures/playerpath.sample.json` (6 sets with a loss in the middle, one DQ)
- Modify: `README.md` (new section "Top 8 & Player Path overlays")

**Interfaces:**
- Consumes: `../sc/playerpath.json` in the Task 2 shape.

Decisions:
- Same header bar as top8 (logo left). Title: team (orange) + player name (white), with the subtitle "ROAD TO <currentRound>" in cyan. Below it, a centered table about 1400 px wide with columns ROUND | OPPONENT | SCORE. The score cell is pink-filled for wins, and loss rows are at 0.5 opacity with the score cell grey. Row height is `min(80px, 820px / rowCount)`.
- Polling, `?src=` override, and missing-file behavior are identical to Task 6. Share no code with it (each page is self-contained).
- README: how to add both as OBS browser sources (local file `_overlays/top8.html` / `_overlays/playerpath.html`, 1920×1080). Say that they need a match loaded from the stream queue and submitted, that the P1/P2 toggle chooses the player, and how the refresh interval works.

- [ ] **Step 1: Write the fixture, page, and README section.**
- [ ] **Step 2: Verify visually** with the same headless Chrome command (output `/tmp/playerpath.png`). Confirm chronological rows, the dimmed loss row, and "DQ".
- [ ] **Step 3: Run** `.venv/bin/python -m unittest discover tests`. Expected: OK.
- [ ] **Step 4: Commit** `git add _overlays/playerpath.html tests/fixtures/playerpath.sample.json README.md && git commit -m "feat: player path overlay and docs"`
