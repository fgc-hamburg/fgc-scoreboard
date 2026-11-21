# Top 8 Bracket & Player Path Overlays — Design

Date: 2026-11-21

## Intent

Add two new full-screen stream overlays built on the existing start.gg integration:

1. **Top 8 bracket**: the event's top 8, with the winners side on top and the losers side
   below. Scores fill in as sets are reported on start.gg.
2. **Player path**: for P1 or P2 of the match on stream (chosen in the dashboard),
   a chronological table of every opponent they beat or lost to on the way to the current
   match, with set scores.

Both behave like the existing overlays: they are local HTML files loaded as OBS browser
sources that read a JSON file under `sc/`. Neither one ever talks to start.gg or the server.

## Goals

- Use the start.gg data the stream queue already relies on. No manual data entry.
- Update automatically while the stream is running.
- Look like our existing overlays (`_overlays/setup.html`: dark background, Valorant
  font, uppercase, pink `#e91e8c` / cyan `#00e5ff` / orange `#ffa500` accents, FGC Hamburg
  logo). The reference screenshot provided the layout only, not the visual style.

## Non-goals

- No support for top 8 run as a separate start.gg phase. Top 8 is always the last rounds
  of one double-elimination bracket (decided by the user).
- No manual editing or overriding of bracket data.
- No in-overlay animation beyond what's needed to look clean. Data changes may simply
  re-render.
- No serving overlays over HTTP, and no requests from overlay pages to the server.

## Anti-goals

- The start.gg token must never reach a file the overlays read, or the browser.
- A start.gg outage or rate-limit must not blank an overlay that's live on stream. On
  error, keep the last good JSON on disk.

## Approaches considered

- **A. Server writes JSON files; overlays poll them (chosen).** A background loop in
  `server.py` fetches from start.gg and writes `sc/top8.json` / `sc/playerpath.json`.
  Overlays poll those files the same way `scoreboard.js` polls `streamcontrol.json`.
  This matches how all existing overlays work and survives brief start.gg outages.
- B. Server hosts the overlays at `http://localhost:8080/overlays/...` and they fetch
  live data. Rejected because these two would be loaded differently from the other
  overlays, and they'd go blank whenever the dashboard is down.
- C. File overlays fetch from `localhost` with CORS headers. Rejected because Chromium in
  OBS restricts `file://` → `http://` requests, which is too fragile for a live show.

## How it works

### 1. Persisting the active match

Today `activeSet = {setId, p1Id, p2Id}` lives only in the dashboard's browser memory.

- On **Submit**, the dashboard POSTs `/api/overlays/active` with
  `{setId, p1Id, p2Id, pathSide}`. `setId`/IDs are `null` when the match was not loaded
  from the queue. This follows the existing rule that nothing goes live until Submit.
  It also means **Swap Players** (which already swaps `p1Id`/`p2Id`) is reflected
  correctly.
- The server saves this to `sc/overlaystate.json` (gitignored) so it survives restarts.
- When `setId` is `null`, the server keeps the last fetched event and its JSON files
  untouched. A manual match shouldn't wipe the bracket off stream.
- After **Report**, `activeSet` is cleared in the browser, but the server keeps the
  last persisted state, so the overlays keep showing the final result.
- **Change tournament** (`/api/streamqueue/clear`) also clears `sc/overlaystate.json`.

### 2. Player-path side toggle

A small **P1 / P2** toggle in the dashboard's stream-queue section, labeled
"Player path". It's part of local state like the other fields: changing it marks the
form dirty, and it's sent on Submit as `pathSide: "p1" | "p2"`. It defaults to `"p1"`.

### 3. Server refresh loop

A daemon thread in `server.py`, active only when `STARTGG_TOKEN` is set and
`overlaystate.json` has a `setId`:

- Runs every `STREAM_QUEUE_REFRESH_SECONDS` (default 30). Also triggers immediately on
  `/api/overlays/active` and after a successful `/api/streamqueue/report`.
- **Resolve context** (once per `setId`, cached): query the set's
  `event { id name tournament { name } }` and `phaseGroup { id }`.
- **Top 8**: fetch all sets of that phase group (paginated). Then build `top8.json`.
- **Player path**: fetch the event's sets filtered by the side's entrant ID. Then build
  `playerpath.json`.
- Writes are atomic (write a temp file, then `os.replace`). On any `StreamQueueError`, log
  it and leave existing files untouched.

All query and normalization code lives in `startgg.py` as pure functions, unit-tested
against fixture responses like the existing tests. `server.py` only schedules and writes.

### 4. Identifying top 8 rounds

start.gg double elimination: winners rounds are positive, losers rounds are negative.

Let `W` = the highest winners round that is **not** a Grand Final reset, and
`L` = the most negative losers round.

| Slot             | Round      | Sets |
|------------------|------------|------|
| Winners Semis    | `W - 2`    | 2    |
| Winners Final    | `W - 1`    | 1    |
| Grand Final      | `W`        | 1    |
| GF Reset         | `W + 1`    | 0–1  |
| Losers Top 8     | `L + 3`    | 2    |
| Losers Quarters  | `L + 2`    | 2    |
| Losers Semis     | `L + 1`    | 1    |
| Losers Final     | `L`        | 1    |

Grand Final reset detection: start.gg places the reset at the highest winners round
when it exists. The builder must confirm against a real bracket how the reset is
represented (separate round number vs. same round, `fullRoundText == "Grand Final Reset"`)
and pick `W` accordingly. Sets within a round are ordered by start.gg's
`identifier`/bracket position so pairs line up with the matches they feed into.

Brackets with fewer than 8 entrants: render whatever rounds exist and leave empty
boxes for the rest. Don't crash.

### 5. JSON contracts

`sc/top8.json`:
```json
{
  "updatedAt": 1784585220,
  "tournamentName": "Bareknuckle Altona 2026",
  "eventName": "Street Fighter 6",
  "rounds": {
    "winnersSemis":   [Match, Match],
    "winnersFinal":   [Match],
    "grandFinal":     [Match],
    "grandFinalReset":[Match] | [],
    "losersTop8":     [Match, Match],
    "losersQuarters": [Match, Match],
    "losersSemis":    [Match],
    "losersFinal":    [Match]
  }
}
```
`Match = {setId, state, p1: Slot, p2: Slot}`,
`Slot = {name, team, score, winner}`. `name` is `null` when the slot is TBD.
`score` is `null` until reported, and `"DQ"` for a DQ (start.gg score `-1`). `winner` is
a boolean. Name/team splitting reuses `_slot()` (gamerTag + sponsor prefix).

`sc/playerpath.json`:
```json
{
  "updatedAt": 1784585220,
  "eventName": "Street Fighter 6",
  "player": {"name": "MrCosta", "team": "FGC HH"},
  "currentRound": "Winners Final",
  "sets": [
    {"round": "Winners Round 1", "opponent": {"name": "...", "team": "..."},
     "score": "2-0", "won": true}
  ]
}
```
`sets` includes only **completed** sets in that event for the entrant, excluding the
active set, sorted by `completedAt` ascending. `score` is from the player's perspective
(theirs first). DQ wins and losses show `"DQ"`. Sets with no opponent (byes) are omitted.

### 6. Overlays

New files `_overlays/top8.html` and `_overlays/playerpath.html`, each self-contained (inline
CSS like `setup.html`, plain JS, no GSAP needed). Each polls its JSON at 500 ms with
cache-busting, and re-renders only when `updatedAt` or content changes.

**Top 8 layout (1920×1080):**
```
[logo]        STREET FIGHTER 6  TOP 8                   (header bar)
 WINNERS SEMIS     WINNERS FINAL     GRAND FINAL   (GF RESET, only if played)
 [p1      2]
 [p2      0]──┐    [         ]       [         ]
 [p3      1]  ├────[         ]───────[         ]
 [p4      2]──┘
 LOSERS TOP 8      LOSERS QUARTERS   LOSERS SEMIS      LOSERS FINAL
 [  ][  ]──────────[  ][  ]──┐
                             ├───────[  ][  ]──────────[  ][  ]
 [  ][  ]──────────[  ][  ]──┘
```
Each player row shows the team in the accent color and the name in white, with the score
in a box on the right. The winner's row is highlighted, the loser's is dimmed. Unreported
scores are blank. The header is `<eventName> TOP 8`, with the tournament name as a smaller
subtitle.

**Player path layout:** a header with the player's team + name and
"Road to <currentRound>". Below it, a table with one row per set: round | opponent
(team + name) | score. Rows are green/accent for wins and dimmed for losses. If the
table has more than ~10 rows, shrink the row height so it still fits in 1080 px.

Both overlays show an empty, styled frame (header only) when the JSON file is missing.

### 7. Server API additions

- `POST /api/overlays/active`: `{setId, p1Id, p2Id, pathSide}` → persists the state and
  triggers a refresh. Returns 403 when there's no token, just like the other queue
  endpoints.
- `GET /api/overlays/status`: `{lastRefresh, lastError}`. This is optional and can be
  used later by the dashboard. It's in the builder's scope only if it's trivial.

## Constraints

- Python standard library only on the server (as in `startgg.py`/`server.py`).
- start.gg rate limit is ~80 requests/min. One refresh is ~1 + ⌈sets/perPage⌉ + 1
  requests. Keep `perPage` within start.gg's complexity limit of 1000 objects per query.
- Overlays must work when opened via `file://` in OBS.

## Left to the builder

- Exact GraphQL field selection and pagination size, verified against the live API
  (including how the GF reset is represented and whether `phaseGroup.sets` supports a
  round filter to cut down pages).
- Exact visual dimensions and spacing within the style constraints above.
- README section documenting the two new browser sources and the P1/P2 toggle.
