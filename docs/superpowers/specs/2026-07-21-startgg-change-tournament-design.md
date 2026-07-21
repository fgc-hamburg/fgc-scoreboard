# Change / Clear Loaded Tournament — Design

Date: 2026-07-21

## Goal

Let the operator switch tournaments (or clear the loaded stream queue) from the
control panel, instead of only being able to load one and never change it
without restarting the server.

## Requirements

- A **Change tournament** button, visible only when a tournament is currently
  loaded.
- Clicking it **confirms first** (clearing is local only; nothing is sent to
  start.gg), then clears the loaded tournament and returns the panel to the
  "paste a URL" setup state.
- Clearing removes the persisted slug/station so a server restart does not
  restore the old tournament.
- After clearing: auto-refresh stops, cards and station dropdown are emptied,
  the loaded-match binding (`activeSet`) is cleared so Report is disabled, and
  the setup input is shown (empty).
- Gated on `STARTGG_TOKEN` like the rest of the feature.

## Non-Goals

- No change to how a tournament is loaded (the existing setup input is reused).
- No start.gg calls — this only clears local/persisted state.

## Architecture

### Server

New endpoint `POST /api/streamqueue/clear`:

- `403` when no token.
- Otherwise persists `{"slug": null, "streamName": null}` via the existing
  `save_queue_config` and returns `200 {"ok": true}`.

### Frontend (`control.html`)

- New **Change tournament** button in the queue header, wrapped alongside the
  existing station/refresh controls; shown whenever `queueConfig.slug` is set
  (in `initQueue` when a slug is restored, and after `loadTournament` succeeds).
- `changeTournament()`:
  - `confirm("Change tournament? This clears the loaded stream queue.")`; abort
    if declined.
  - `POST /api/streamqueue/clear`; on non-OK show the inline queue error.
  - On success: stop `queueTimer`; reset `queueConfig.slug`/`streamName` to null,
    `queueStations = []`, `activeSet = null`, `updateReportButton()`; empty the
    cards and hide the station controls and the Change button; show the setup
    input and clear the URL field; clear any queue error.

## Testing

- Server smoke test: `POST /api/streamqueue/clear` returns `403` without a token;
  with a token returns `200` and blanks `sc/streamqueue.json`.
- `node --check` on the extracted control-panel JS.
- Frontend verified manually (button appears when loaded; clearing returns to the
  setup input and disables Report).
