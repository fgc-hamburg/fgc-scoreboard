# Top 8 From Connected Tournament — Follow-up Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `sc/top8.json` is driven by the tournament connected in the stream queue, not by a submitted match. The top 8 overlay fills in as soon as a tournament is loaded, even when the stream queue is empty.

**Why (bug found in use):** a completed tournament (`tekken-8-at-fgc-hamburg`) had an empty stream queue, so no match could be submitted, `overlaystate.json` never got a `setId`, and the top 8 stayed empty.

**Architecture change:** the refresh loop now has two independent jobs:
- **top 8:** needs the queue config `slug` (from `sc/streamqueue.json`) plus a resolved event.
- **player path:** needs the active `setId` (unchanged).

Each job has its own error path. A failure in one never stops the other from writing its own file. Each file is still written atomically and left untouched on its own error.

**Spec:** `docs/superpowers/specs/2026-11-21-top8-and-player-path-overlays-design.md`. This plan supersedes its §3 for top 8 only. The event comes from the tournament: auto-selected when there's one event, picked in the dashboard when there are several. The top 8 header shows the start.gg **tournament name** as the main title, followed by " TOP 8", with the event name as the subtitle (requested by the user).

## Global Constraints
- Stdlib only. The token never reaches `sc/` files or the browser.
- On a start.gg error, the affected overlay JSON file is left untouched.
- The refresh interval is `STREAM_QUEUE_REFRESH_SECONDS`.
- Tests: `.venv/bin/python -m unittest discover tests`.
- Overlay text is inserted via `textContent` only.

## Review Focus
1. **Tournament with several events and none chosen:** `top8.json` is untouched, and status `lastError` reads `"Choose an event for the top 8"`. The path job still runs. Test: `test_refresh_top8_requires_event_choice_when_multiple`.
2. **Changing tournament:** the event choice and the top 8 cache are reset, and the loop is woken. The new tournament's top 8 is written on the next refresh. Test: `test_refresh_top8_cache_keyed_by_slug_and_event`.
3. **Event with no double-elimination phase, or whose last DE phase has several phase groups (pools):** raise `StreamQueueError("No single double-elimination bracket in this event")`. Test: `test_pick_bracket_phase_group_*`.
4. **Path job fails while top 8 succeeds** (and the reverse): the healthy file is still written. Test: `test_refresh_jobs_are_independent`.
5. **No tournament loaded:** neither top 8 fetches nor writes happen. Test: `test_refresh_top8_noop_without_slug`.

---

### Task A: Backend — tournament-driven top 8

**Files:** modify `startgg.py`, `overlays.py`, `server.py`, `tests/test_overlays.py`, `tests/test_startgg.py`

**Interfaces produced:**
- `startgg.fetch_tournament_events(slug, token) -> dict` returns `{"tournamentName": str, "events": [{"id", "name", "phases": [{"id", "phaseOrder", "bracketType", "phaseGroupIds": [ids]}]}]}`. Query: `tournament(slug:$slug){ name events{ id name phases{ id phaseOrder bracketType phaseGroups(query:{perPage:50}){ nodes{ id } } } } }`. A null tournament raises `StreamQueueError("Tournament not found")`.
- `overlays.pick_bracket_phase_group(event: dict) -> id`: take the phases with `bracketType == "DOUBLE_ELIMINATION"`, then the one with the highest `phaseOrder`. It must have exactly one phase group. Otherwise raise `StreamQueueError("No single double-elimination bracket in this event")`.
- `overlays.resolve_event(events: list, event_id) -> dict`: if `event_id` is set, return the matching event (by `_same_id`), or raise `StreamQueueError("Selected event not found")`. Else, if there's exactly one event, return it. Else raise `StreamQueueError("Choose an event for the top 8")`.
- `DEFAULT_STATE` gains `"eventId": None`. `merge_active_state` preserves `eventId` from `prev`.
- `overlays.set_event(prev: dict, event_id) -> dict` returns prev with `eventId` replaced.
- Split `refresh` into:
  - `refresh_top8(slug, event_id, token, sc_dir, now, cache: dict) -> bool`. With no slug it returns False and makes no calls. The cache key is `(slug, str(event_id))`, and the cached value is `{"tournamentName", "event", "phaseGroupId"}`. Then it calls `fetch_phase_group_sets` → `build_top8(sets, tournamentName, event["name"], now)` → `write_json_atomic(sc_dir/"top8.json")`, and returns True.
  - `refresh_path(state, token, sc_dir, now, context_cache) -> bool`. This is the current behavior restricted to `playerpath.json`, still using `fetch_set_context`. With no setId it returns False.
  - `refresh_all(state, slug, token, sc_dir, now, caches: dict) -> dict` runs both jobs. Each is wrapped so that one's exception doesn't skip the other. It returns `{"top8": True|False|"<error str>", "path": True|False|"<error str>"}`. `caches` holds the keys `"top8"` and `"path"`.
  - Remove the old `refresh`, and update its tests to target `refresh_path` / `refresh_all`.
- Server:
  - The loop loads the overlay state and `startgg.load_queue_config(QUEUE_CONFIG_PATH)["slug"]`, then calls `refresh_all`. `OVERLAY_STATUS` becomes `{"lastRefresh", "lastError", "top8Error", "pathError"}`. `lastRefresh` is stamped when either job returned True. `lastError` is the joined non-empty error strings, or None.
  - `GET /api/overlays/events` (token-gated) returns `{"events": [{"id","name"}], "eventId": <state eventId>}` from `fetch_tournament_events` for the current slug. It returns 409 `{"error":"No tournament loaded"}` when there's no slug, and 502 on a `StreamQueueError`.
  - `POST /api/overlays/event` `{eventId}` (token-gated) saves it via `set_event` and wakes the loop.
  - `_handle_post_tournament` (on success) and `_handle_post_clear`: reset `eventId` to None in the overlay state (keep the other fields on tournament load, reset everything on clear), clear the top 8 cache, and wake the loop.

- [ ] Tests first, covering each Review Focus item plus:
  - `test_fetch_tournament_events_shapes_phase_group_ids`
  - `test_resolve_event_single_auto`
  - `test_resolve_event_by_id_string_vs_int`
  - `test_refresh_top8_writes_with_tournament_name` (asserts `tournamentName` is from the tournament and `eventName` from the event)
- [ ] Implement. Then run the full suite, which should be OK.
- [ ] Live read-only check: run `refresh_top8` against slug `tekken-8-at-fgc-hamburg` into a temp dir (not `sc/`), and confirm all the top 8 keys are filled with real names and scores. Don't print the token.
- [ ] Commit.

### Task B: Dashboard event picker + overlay header

**Files:** modify `control.html`, `_overlays/top8.html`, `README.md`

- Dashboard:
  - After a tournament loads (and on init when a slug exists), `GET /api/overlays/events`. If there's more than one event, show a `<select id="overlayEvent">` labeled "Top 8 event" next to the station picker, preselecting `eventId`. On change, `POST /api/overlays/event` immediately (this is a setup choice, like the station picker, and doesn't need Submit).
  - Hide the picker when there's one event or the token is disabled.
  - Show request errors via `setQueueError`.
- `top8.html` header: main title = `tournamentName` + pink " TOP 8". Subtitle (cyan) = `eventName`. Re-screenshot against the fixture to confirm it still fits; long tournament names shrink to fit on one line.
- README: the top 8 now appears as soon as a tournament is connected, with an event picker for multi-event tournaments. The player path still needs a submitted match. Remove the sentence saying the previous tournament stays until a match is submitted, and replace it with: changing tournament switches the top 8 on the next refresh.
- [ ] Implement, headless-Chrome screenshot of `top8.html` with the fixture, then commit.
