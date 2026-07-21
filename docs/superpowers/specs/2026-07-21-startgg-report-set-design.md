# Report Set Result to Start.gg — Design

Date: 2026-07-21

## Goal

Add a **Report to start.gg** button to the control panel that submits the
current match's result — with the score the operator set — back to the start.gg
bracket, using the `reportBracketSet` mutation. Builds on the stream-queue
integration (a match must be loaded from the queue to be reportable).

## Requirements

- Report the result of the currently loaded set to start.gg, including the
  **set score** the players/operator set (not just the winner).
- The winner is the side with the **higher score**.
- Reporting is only possible for a match **loaded from the stream queue** (we
  need the set ID and both entrant IDs; a hand-typed match has none).
- **Confirm before submitting** (writes to a live bracket): a `confirm()` naming
  both players and the score.
- After a **successful** report: show a success message, **refresh the queue**
  (the reported set drops off), and clear the active-set binding.
- **Swap** keeps the report valid: the tracked entrant IDs swap in lockstep with
  names and scores.
- Editing a player **name** disables Report (the binding may no longer match the
  displayed match); editing a **score** does not.
- Tie scores and 0–0 cannot be reported (no determinable winner / nothing to
  report) — blocked in the UI and rejected by the server.
- The bearer token stays server-side; the mutation runs in `server.py`.
- If `STARTGG_TOKEN` is absent the whole stream-queue feature (including this
  button) stays hidden, exactly as today.

## Non-Goals

- No per-game detail beyond what is needed to reflect the set score (no stage or
  character selections).
- No editing/overriding the winner independent of the score.
- No reporting of matches not sourced from the queue.

## Interaction with the existing Submit

The panel has two distinct write actions:

- **Submit** — writes `sc/streamcontrol.json` for the OBS overlay. Always valid;
  unaffected by swap or manual typing.
- **Report** — writes the result to the start.gg bracket. Bound to a specific
  set ID + entrant IDs; validity rules above.

## Architecture

### 1. Carry IDs through the queue

Extend the stream-queue query so each set carries its ID and each entrant its ID:

```graphql
sets {
  id
  fullRoundText
  round
  slots { entrant { id name } }
}
```

`normalize_stream_queue` gains three fields per set:

- `setId` — from `set.id`
- `p1Id` / `p2Id` — from `slots[0]/[1].entrant.id` (null when the entrant or
  slot is null)

Card rendering (`p1`/`p2` names) is unchanged.

### 2. Report logic (`startgg.py`, pure + testable)

Refactor the existing GraphQL POST into a shared helper:

- `_graphql(query, variables, token) -> dict` — POSTs to `API_URL`, raises
  `StreamQueueError` on HTTP/network/GraphQL errors, returns the `data` object.
  `_post_graphql`/`fetch_stream_queue` are re-expressed in terms of it.

New:

- `REPORT_SET_MUTATION` — the `reportBracketSet(setId, winnerId, gameData)`
  mutation string returning `id` and `state`.
- `build_report_variables(set_id, p1_id, p2_id, p1_score, p2_score) -> dict`
  returning `{"setId", "winnerId", "gameData"}`.
  - Coerces scores to `int`. Raises `StreamQueueError` when: either entrant ID is
    missing/None, scores are equal (tie), or both scores are 0.
  - `winnerId` = `p1_id` if `p1_score > p2_score` else `p2_id`.
  - `gameData`: one entry per game, `gameNum` starting at 1. The first
    `p1_score` games have `winnerId = p1_id`, `entrant1Score = 1`,
    `entrant2Score = 0`; the next `p2_score` games have `winnerId = p2_id`,
    `entrant1Score = 0`, `entrant2Score = 1`. (Per-game score of 1/0 is a generic
    "this side won this game"; the resulting set score equals `p1_score:p2_score`.)
- `report_set(set_id, p1_id, p2_id, p1_score, p2_score, token) -> dict` — builds
  variables, runs the mutation via `_graphql`, returns the response data. Raises
  `StreamQueueError` on validation or API failure.

### 3. Server endpoint

`POST /api/streamqueue/report`, body
`{setId, p1Id, p2Id, p1Score, p2Score}`:

- `403` when no token.
- `400` on validation failure (tie / 0–0 / missing ID) — message from
  `StreamQueueError`.
- `502` on start.gg error (e.g. no permission, set already reported).
- `200 {"ok": true}` on success.

The handler distinguishes validation errors (400) from API errors (502) by
calling `build_report_variables` first (400 on failure), then `_graphql`
(502 on failure).

### 4. Frontend (`control.html`)

- New state `activeSet` — `{setId, p1Id, p2Id}` or `null`.
- `loadMatch(set)` sets `activeSet = {setId: set.setId, p1Id: set.p1Id,
  p2Id: set.p2Id}` (in addition to filling the form). If `setId`, `p1Id`, or
  `p2Id` is missing, `activeSet` is left `null`.
- `swapPlayers()` also swaps `activeSet.p1Id`/`p2Id` when `activeSet` is set.
- The two player **name** inputs get an extra listener that clears `activeSet`
  (score inputs are buttons, not text, so they are unaffected).
- `updateReportButton()` enables the Report button only when
  `queueConfig.enabled` and `activeSet` is non-null; otherwise disabled. Called
  from `renderAll`, `loadMatch`, `swapPlayers`, the name-edit listener, and after
  reporting.
- **Report button** in the `.actions` row, before Submit, shown only when the
  feature is enabled.
- `reportSet()`:
  - Guard: `activeSet` set; scores not equal and not both 0 — else
    `showStatus(...)` error and return.
  - `winnerName` = the higher-scored side's name; `confirm()` text:
    `Report <p1Name> <p1Score>–<p2Score> <p2Name> to start.gg?`.
  - POST `{setId, p1Id, p2Id, p1Score, p2Score}` to `/api/streamqueue/report`.
  - On `ok`: `showStatus("Reported to start.gg.", "ok")`, `activeSet = null`,
    `updateReportButton()`, `refreshQueue()`.
  - On error: `showStatus("Report failed: " + error, "err")`.

## Error Handling

- No token: endpoint `403`; button never shown.
- Tie / 0–0 / missing ID: blocked client-side; server returns `400`.
- start.gg mutation error (permission, already reported, network): `502`; UI
  shows the message and leaves the form untouched.

## Testing

`unittest` in `tests/test_startgg.py`:

- `build_report_variables`: `3–1` → `winnerId == p1_id`, 4 games, correct
  per-game winners/scores; `0–3` → `winnerId == p2_id`; tie `2–2` raises; `0–0`
  raises; missing entrant ID raises.
- `report_set`: with `_graphql` mocked, asserts it is called with the mutation
  and the built variables and returns the data; a raising `_graphql` propagates
  as `StreamQueueError`.
- `normalize_stream_queue`: extended sample asserts `setId`, `p1Id`, `p2Id`
  (including null entrant → null IDs).

Frontend verified manually (button disabled until a card is loaded; disabled
after a name edit; stays enabled after swap and score edits; confirm dialog; tie
blocked; success refreshes the queue).
