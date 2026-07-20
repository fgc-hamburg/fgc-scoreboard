# Start.gg Stream Queue Integration — Design

Date: 2026-07-21

## Goal

Extend the scoreboard control panel so bracket runners can load upcoming
matches directly from a tournament's **start.gg stream queue**, instead of
typing player names by hand. A new section below the scoreboard shows cards for
upcoming matches; clicking one populates the form.

## Requirements

- Read the stream queue from the start.gg GraphQL API using a bearer token.
- **If no bearer token is configured, the feature is fully disabled**: no UI
  section, no endpoints doing work, no auto-refresh.
- Auto-refresh the queue on an interval (default 30s), configured via `.env`.
- A manual **Refresh** button.
- The user pastes their tournament URL (e.g.
  `https://www.start.gg/tournament/test-tournament-do-not-publish-1/details`);
  only the segment after `/tournament/` (`test-tournament-do-not-publish-1`) is
  used as the query parameter, the rest is trimmed.
- A tournament can have multiple stream stations. Fetch the stream name and let
  the user pick which station's queue to display.
- The tournament slug and chosen station **persist across restarts** (saved to a
  file server-side).
- **Clicking a match card** populates the form: player names ← entrant names,
  round ← `fullRoundText`, scores reset to `0:0`, and Team / 2nd-player fields
  **cleared**. It marks the form dirty but does **not** auto-submit — the user
  presses Submit. Editing the form never mutates the queue.

## Non-Goals

- No auto-submit of a match to the scoreboard on card click.
- No writing back to start.gg (read-only).
- No individual duo-member names for 2XKO (the query returns only the single
  entrant/team name per slot).

## Architecture

### Token stays server-side

The bearer token must never reach the browser. All start.gg GraphQL calls are
made by `server.py` using stdlib `urllib.request` (no new pip dependencies). The
browser talks only to our own `/api/...` endpoints. This also avoids CORS.

### Configuration (`.env`)

Loaded via `python-dotenv` (already present in `.venv`; `server.py` will call
`load_dotenv()` at startup — it currently does not).

- `STARTGG_TOKEN` — bearer token. Absent/empty ⇒ feature disabled.
- `STREAM_QUEUE_REFRESH_SECONDS` — auto-refresh interval in seconds, default `30`.

`.env` gets commented example entries for both.

### Persisted runtime state

File `sc/streamqueue.json` (added to `.gitignore`):

```json
{ "slug": "test-tournament-do-not-publish-1", "streamName": "Main Stage" }
```

`streamName` may be `null` (no station chosen yet ⇒ default to the first).

### GraphQL query

Endpoint: `https://api.start.gg/gql/alpha`, `POST`, headers
`Authorization: Bearer <token>` and `Content-Type: application/json`.

```graphql
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
```

Variables: `{ "tourneySlug": "<slug>" }`.

### Normalization

`streamQueue` (may be `null`) → list of stations:

```json
[
  {
    "streamName": "Main Stage",
    "sets": [
      { "fullRoundText": "Winners Final", "round": 1, "p1": "MrCosta", "p2": "FGC Hamburg | Community" },
      { "fullRoundText": "Grand Final",   "round": 2, "p1": null,       "p2": null }
    ]
  }
]
```

- `streamName` falls back to `"Stream"` when `stream` or `streamName` is missing.
- `p1`/`p2` come from `slots[0]`/`slots[1]` `entrant.name`; a `null` entrant (or
  missing slot) yields `null`, displayed as **TBD** in the UI.

### Server endpoints

- `GET /api/streamqueue/config`
  → `{ enabled, refreshSeconds, slug, streamName }`. Never includes the token.
  `enabled` is `false` when the token is absent. Drives whether the UI renders.
- `POST /api/streamqueue/tournament` `{ url }`
  → parses the slug from the URL, persists it (resets `streamName` to `null`),
  fetches and returns the normalized queue. `400` on an unparseable URL.
- `GET /api/streamqueue`
  → fetches from start.gg for the saved slug, returns the normalized stations.
  `409`/appropriate error if no slug is saved yet.
- `POST /api/streamqueue/station` `{ streamName }`
  → persists the chosen station.

All stream-queue endpoints return `403` (feature disabled) when the token is
absent. Network / GraphQL / bad-token failures return a `502`-class error with a
message; the UI shows an inline error and keeps the last-good cards.

Slug parsing: take the path segment immediately after `/tournament/`, up to the
next `/`. Accept full URLs, `start.gg`/`smash.gg` hosts, and a bare slug.

### Frontend (`control.html`)

A new **Stream Queue** section rendered below the actions row.

- On init, `GET /api/streamqueue/config`. If `enabled` is false → do not render
  the section, do not start any timer.
- If enabled but no `slug` → inline text input + **Load tournament** button
  prompting for the start.gg tournament URL.
- Once a slug is loaded → a **station dropdown** (populated from the returned
  stream names, selection persisted), a manual **Refresh** button, and a grid of
  match **cards**. Each card shows `fullRoundText` and `P1 vs P2` (TBD for null).
- **Auto-refresh** every `refreshSeconds`; manual Refresh also available. Only
  the selected station's sets are displayed.
- **Card click** → populate form (`p1Name`, `p2Name`, `round`, scores `0`, clear
  `p1Team`/`p2Team`/`p1Name2`/`p1Team2`/`p2Name2`/`p2Team2`), mark dirty, do not
  submit. Reuses the existing render/dirty machinery.
- The existing 2s `streamcontrol.json` poll is unchanged; the queue timer is
  independent.

## Error Handling

- Token missing: endpoints return `403`; UI never shows the section.
- Unparseable tournament URL: `POST /tournament` returns `400`; UI shows a
  message by the input.
- start.gg error / network failure / GraphQL `errors`: endpoint returns an error
  status with a message; UI shows an inline error and retains the last-good
  cards rather than clearing them.
- Empty/`null` `streamQueue`: normalized to an empty station list; UI shows
  "No matches queued".

## Testing

Python `pytest` tests for the pure logic (HTTP to start.gg mocked):

- Slug extraction: full URL with `/details`, trailing slash, query string, bare
  slug, unparseable input.
- Normalization: multi-station, null entrants, missing `stream`/`streamName`,
  missing slot, `null` `streamQueue`.
- Config gating: `enabled` reflects token presence; token never serialized into
  the config response.

Frontend behavior verified manually (feature hidden without token; load
tournament; station switch; card click populates + clears correctly without
submitting; auto-refresh cadence).
