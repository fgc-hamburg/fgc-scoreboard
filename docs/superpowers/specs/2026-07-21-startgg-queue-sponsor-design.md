# Stream Queue Sponsor Tags — Design

Date: 2026-07-21

## Goal

Load each entrant's **sponsor tag** from the start.gg stream queue so that
clicking a match card fills the panel's **Team / Sponsor** field, instead of
leaving it blank.

## Background: which field

Per the start.gg schema, the sponsor tag is `Entrant.participants[].prefix`
(String — "the prefix that the user set for this Tournament, e.g. `C9`"). The
clean player tag is `Participant.gamerTag`. `Entrant.name` combines them, e.g.
`"FGC Hamburg | Community"` = prefix `"FGC Hamburg"` + gamerTag `"Community"`.

## Requirements

- Extend the stream-queue query to fetch `participants { prefix gamerTag }` on
  each entrant.
- Fill Name and Team/Sponsor with a **clean split** (no prefix duplication):
  - **Single-participant entrant (singles):** Name = `gamerTag` (fallback to
    `entrant.name` when `gamerTag` is empty), Team/Sponsor = `prefix` (or empty).
  - **Team/duo entrant (0 or 2+ participants, e.g. 2XKO):** Name =
    `entrant.name`, Team/Sponsor = empty. Unchanged from current behavior.
- `loadMatch` populates `p1Team`/`p2Team` from the queue instead of clearing
  them. It still clears the 2XKO 2nd-player fields (`p1Name2`, `p1Team2`,
  `p2Name2`, `p2Team2`), which the queue does not provide.

## Non-Goals

- No per-member sponsor handling for 2XKO/duo entrants.
- No change to how the scoreboard overlay renders team/sponsor.

## Architecture

### Query

```graphql
slots {
  entrant {
    id
    name
    participants { prefix gamerTag }
  }
}
```

(When report-set is also implemented, this is the same `slots.entrant` block
that gains `id`; the two changes are compatible.)

### Normalization (`startgg.py`)

`normalize_stream_queue` gains `p1Team`/`p2Team` per set. Per slot:

```
entrant is null            -> name = None,          team = ""
exactly 1 participant      -> name = gamerTag or entrant.name,
                              team = prefix or ""
0 or 2+ participants        -> name = entrant.name,  team = ""
```

Resulting per-set shape: `{fullRoundText, round, p1, p2, p1Team, p2Team}`
(plus `setId`/`p1Id`/`p2Id` when report-set lands). Card rendering (`p1`/`p2`)
is unchanged.

### Frontend (`control.html`)

`loadMatch(set)` sets:

- `state.p1Team = set.p1Team || ""`, `state.p2Team = set.p2Team || ""`
  (replacing the current lines that clear them).
- 2XKO 2nd-player fields (`p1Name2`, `p1Team2`, `p2Name2`, `p2Team2`) are still
  cleared.

## Testing

`unittest` in `tests/test_startgg.py`:

- Single-participant entrant → `p1` = gamerTag, `p1Team` = prefix.
- gamerTag empty → `p1` falls back to `entrant.name`.
- Two-participant (team) entrant → `p1` = `entrant.name`, `p1Team` = "".
- Null entrant → `p1` = None, `p1Team` = "".

Frontend verified manually (loading a singles match fills the Team/Sponsor
field; a team match leaves it blank).
