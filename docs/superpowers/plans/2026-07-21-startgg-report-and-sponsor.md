# Start.gg Report Set + Queue Sponsor Tags — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Report to start.gg" button that reports the loaded set's result (with the operator's score) via `reportBracketSet`, and load each entrant's sponsor tag from the queue into the Team/Sponsor field.

**Architecture:** Extend the stream-queue query + `normalize_stream_queue` once to carry set/entrant IDs (for reporting) and participant prefix/gamerTag (for the clean name/sponsor split). Add pure report-variable building and a mutation call to `startgg.py`, a `/api/streamqueue/report` endpoint to `server.py`, and the button + active-set tracking to `control.html`.

**Tech Stack:** Python 3 standard library only, vanilla JS, `unittest`.

## Global Constraints

- **No new pip dependencies.** Standard library only.
- **The bearer token must never be sent to the browser.** The mutation runs in `server.py`; endpoints never serialize the token.
- **Reporting is gated on `STARTGG_TOKEN`** (like the rest of the queue feature) and only possible for a match loaded from the queue (needs `setId` + both entrant IDs).
- **Winner = higher score.** Ties and 0–0 are not reportable (blocked client-side, rejected server-side with `400`).
- **Sponsor mapping (clean split):** single-participant entrant → Name = `gamerTag` (fallback `entrant.name`), Team = `prefix` (or `""`); 0/2+ participants → Name = `entrant.name`, Team = `""`.
- Tests are stdlib `unittest`, run directly: `python3 tests/test_startgg.py -v`.

---

### Task 1: Query + normalization — IDs and sponsor split

**Files:**
- Modify: `startgg.py` (`STREAM_QUEUE_QUERY`, `normalize_stream_queue`)
- Test: `tests/test_startgg.py`

**Interfaces:**
- Produces: each normalized set now has `setId`, `p1Id`, `p2Id`, `p1Team`, `p2Team` in addition to `fullRoundText`, `round`, `p1`, `p2`.

- [ ] **Step 1: Update the test data and add failing tests**

In `tests/test_startgg.py`, replace the `SAMPLE_DATA` block with this (adds set/entrant `id`s; entrants still have no `participants`, so names stay `entrant.name`):

```python
SAMPLE_DATA = {
    "tournament": {
        "streamQueue": [
            {
                "stream": {"streamName": "Main Stage"},
                "sets": [
                    {
                        "id": 501,
                        "fullRoundText": "Winners Final",
                        "round": 1,
                        "slots": [
                            {"entrant": {"id": 10, "name": "MrCosta"}},
                            {"entrant": {"id": 20, "name": "FGC Hamburg | Community"}},
                        ],
                    },
                    {
                        "id": 502,
                        "fullRoundText": "Grand Final",
                        "round": 2,
                        "slots": [{"entrant": None}, {"entrant": None}],
                    },
                ],
            }
        ]
    }
}


SPONSOR_DATA = {
    "tournament": {
        "streamQueue": [
            {
                "stream": {"streamName": "S"},
                "sets": [
                    {
                        "id": 1,
                        "fullRoundText": "R1",
                        "round": 1,
                        "slots": [
                            {"entrant": {"id": 10, "name": "FGC Hamburg | Community",
                                         "participants": [{"prefix": "FGC Hamburg",
                                                           "gamerTag": "Community"}]}},
                            {"entrant": {"id": 20, "name": "Buzz",
                                         "participants": [{"prefix": None,
                                                           "gamerTag": "Buzz"}]}},
                        ],
                    }
                ],
            }
        ]
    }
}
```

Append these test classes before the `if __name__` block:

```python
class IdTests(unittest.TestCase):
    def test_includes_set_and_entrant_ids(self):
        sets = normalize_stream_queue(SAMPLE_DATA)[0]["sets"]
        self.assertEqual(sets[0]["setId"], 501)
        self.assertEqual(sets[0]["p1Id"], 10)
        self.assertEqual(sets[0]["p2Id"], 20)
        self.assertIsNone(sets[1]["p1Id"])
        self.assertIsNone(sets[1]["p2Id"])

    def test_no_participants_gives_empty_team(self):
        s = normalize_stream_queue(SAMPLE_DATA)[0]["sets"][0]
        self.assertEqual(s["p1Team"], "")
        self.assertEqual(s["p2Team"], "")


class SponsorTests(unittest.TestCase):
    def test_single_participant_splits_name_and_prefix(self):
        s = normalize_stream_queue(SPONSOR_DATA)[0]["sets"][0]
        self.assertEqual(s["p1"], "Community")
        self.assertEqual(s["p1Team"], "FGC Hamburg")

    def test_missing_prefix_gives_empty_team(self):
        s = normalize_stream_queue(SPONSOR_DATA)[0]["sets"][0]
        self.assertEqual(s["p2"], "Buzz")
        self.assertEqual(s["p2Team"], "")

    def test_empty_gamertag_falls_back_to_name(self):
        data = {"tournament": {"streamQueue": [{"sets": [{"id": 1, "fullRoundText": "R",
            "round": 1, "slots": [
                {"entrant": {"id": 1, "name": "Fallback",
                             "participants": [{"prefix": "P", "gamerTag": None}]}},
                {"entrant": None}]}]}]}}
        s = normalize_stream_queue(data)[0]["sets"][0]
        self.assertEqual(s["p1"], "Fallback")
        self.assertEqual(s["p1Team"], "P")

    def test_two_participants_uses_entrant_name(self):
        data = {"tournament": {"streamQueue": [{"sets": [{"id": 1, "fullRoundText": "R",
            "round": 1, "slots": [
                {"entrant": {"id": 1, "name": "Team Rocket", "participants": [
                    {"prefix": "TR", "gamerTag": "Jessie"},
                    {"prefix": "TR", "gamerTag": "James"}]}},
                {"entrant": None}]}]}]}}
        s = normalize_stream_queue(data)[0]["sets"][0]
        self.assertEqual(s["p1"], "Team Rocket")
        self.assertEqual(s["p1Team"], "")
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python3 tests/test_startgg.py -v`
Expected: FAIL — `IdTests`/`SponsorTests` fail with `KeyError: 'setId'` / `'p1Team'`.

- [ ] **Step 3: Update the query and rewrite normalization**

In `startgg.py`, replace the `slots` line inside `STREAM_QUEUE_QUERY`:

```graphql
        slots { entrant { name } }
```

with:

```graphql
        slots { entrant { id name participants { prefix gamerTag } } }
```

Also add `id` under `sets` (put it right after the `sets {` line) so the block reads:

```graphql
      sets {
        id
        fullRoundText
        round
        slots { entrant { id name participants { prefix gamerTag } } }
      }
```

Replace the entire `normalize_stream_queue` function with:

```python
def _slot(slots, i):
    """Return (entrant_id, name, team) for slot i, or (None, None, "")."""
    if i < len(slots):
        ent = slots[i].get("entrant")
        if ent:
            parts = ent.get("participants") or []
            if len(parts) == 1:
                p = parts[0] or {}
                name = p.get("gamerTag") or ent.get("name")
                team = p.get("prefix") or ""
            else:
                name = ent.get("name")
                team = ""
            return ent.get("id"), name, team
    return None, None, ""


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
            p1_id, p1, p1_team = _slot(slots, 0)
            p2_id, p2, p2_team = _slot(slots, 1)
            sets_out.append({
                "setId": s.get("id"),
                "fullRoundText": s.get("fullRoundText"),
                "round": s.get("round"),
                "p1": p1,
                "p2": p2,
                "p1Id": p1_id,
                "p2Id": p2_id,
                "p1Team": p1_team,
                "p2Team": p2_team,
            })
        stations.append({"streamName": stream_name, "sets": sets_out})
    return stations
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS (existing tests + `IdTests` + `SponsorTests`).

- [ ] **Step 5: Commit**

```bash
git add startgg.py tests/test_startgg.py
git commit -m "feat: carry set/entrant IDs and sponsor tags through the queue"
```

---

### Task 2: `_graphql` refactor + `build_report_variables`

**Files:**
- Modify: `startgg.py`
- Test: `tests/test_startgg.py`

**Interfaces:**
- Produces:
  - `_graphql(query, variables, token) -> dict` (shared POST helper; raises `StreamQueueError`).
  - `build_report_variables(set_id, p1_id, p2_id, p1_score, p2_score) -> dict` returning `{"setId", "winnerId", "gameData"}`; raises `StreamQueueError` on tie, 0–0, missing entrant ID, or non-integer scores.

- [ ] **Step 1: Write the failing tests**

Update the `from startgg import (...)` block to add the new names, and add `import startgg` below the imports:

```python
from startgg import (
    parse_slug,
    normalize_stream_queue,
    fetch_stream_queue,
    StreamQueueError,
    load_queue_config,
    save_queue_config,
    build_config_response,
    build_report_variables,
    report_set,
)
import startgg
```

Append this test class before the `if __name__` block:

```python
class BuildReportTests(unittest.TestCase):
    def test_3_1_winner_and_games(self):
        v = build_report_variables(99, 10, 20, 3, 1)
        self.assertEqual(v["setId"], 99)
        self.assertEqual(v["winnerId"], 10)
        self.assertEqual([g["winnerId"] for g in v["gameData"]], [10, 10, 10, 20])
        self.assertEqual([g["gameNum"] for g in v["gameData"]], [1, 2, 3, 4])

    def test_0_3_winner_is_p2(self):
        v = build_report_variables(99, 10, 20, 0, 3)
        self.assertEqual(v["winnerId"], 20)
        self.assertEqual(len(v["gameData"]), 3)

    def test_tie_raises(self):
        with self.assertRaises(StreamQueueError):
            build_report_variables(99, 10, 20, 2, 2)

    def test_zero_zero_raises(self):
        with self.assertRaises(StreamQueueError):
            build_report_variables(99, 10, 20, 0, 0)

    def test_missing_entrant_raises(self):
        with self.assertRaises(StreamQueueError):
            build_report_variables(99, None, 20, 3, 0)

    def test_non_integer_raises(self):
        with self.assertRaises(StreamQueueError):
            build_report_variables(99, 10, 20, "x", 1)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 tests/test_startgg.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_report_variables'`.

- [ ] **Step 3: Refactor `_graphql` and add `build_report_variables`**

In `startgg.py`, replace the entire `_post_graphql` function with this shared helper:

```python
def _graphql(query, variables, token):
    payload = json.dumps({"query": query, "variables": variables}).encode()
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
```

Change `fetch_stream_queue` to use it:

```python
def fetch_stream_queue(slug, token):
    """Fetch and normalize the stream queue for a tournament slug."""
    return normalize_stream_queue(
        _graphql(STREAM_QUEUE_QUERY, {"tourneySlug": slug}, token))
```

Add `build_report_variables` (place it after `fetch_stream_queue`):

```python
def build_report_variables(set_id, p1_id, p2_id, p1_score, p2_score):
    """Build reportBracketSet variables from the panel's set score.

    Winner is the higher-scored side. One synthetic game per point reproduces
    the set score on start.gg. Raises StreamQueueError when unreportable.
    """
    if p1_id is None or p2_id is None:
        raise StreamQueueError("Both entrants must be known to report")
    try:
        p1_score = int(p1_score)
        p2_score = int(p2_score)
    except (TypeError, ValueError):
        raise StreamQueueError("Scores must be integers")
    if p1_score < 0 or p2_score < 0:
        raise StreamQueueError("Scores must be non-negative")
    if p1_score == 0 and p2_score == 0:
        raise StreamQueueError("Nothing to report (0-0)")
    if p1_score == p2_score:
        raise StreamQueueError("Cannot report a tie")
    winner_id = p1_id if p1_score > p2_score else p2_id
    game_data = []
    num = 1
    for _ in range(p1_score):
        game_data.append({"winnerId": p1_id, "gameNum": num,
                          "entrant1Score": 1, "entrant2Score": 0})
        num += 1
    for _ in range(p2_score):
        game_data.append({"winnerId": p2_id, "gameNum": num,
                          "entrant1Score": 0, "entrant2Score": 1})
        num += 1
    return {"setId": set_id, "winnerId": winner_id, "gameData": game_data}
```

Note: `report_set` is imported by the test but defined in Task 3. This step's tests only exercise `build_report_variables`; the import line already lists `report_set`, so **Task 3 must land for the module to import** — that is fine because we run this task's tests only after Step 4 here, which will still fail on the missing `report_set` import. To keep Step 4 green, also add a minimal stub now that Task 3 fills in:

```python
def report_set(set_id, p1_id, p2_id, p1_score, p2_score, token):
    return submit_report(
        build_report_variables(set_id, p1_id, p2_id, p1_score, p2_score), token)
```

and the `submit_report` + mutation constant (fully specified in Task 3). To avoid a broken intermediate state, implement Task 3's Step 3 additions here as well before running. (The two tasks are split only for review clarity; land them together.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add startgg.py tests/test_startgg.py
git commit -m "refactor: shared _graphql helper; add report-variable builder"
```

---

### Task 3: `report_set` + mutation

**Files:**
- Modify: `startgg.py`
- Test: `tests/test_startgg.py`

**Interfaces:**
- Produces:
  - `REPORT_SET_MUTATION` (string).
  - `submit_report(variables, token) -> dict` — runs the mutation via `_graphql`.
  - `report_set(set_id, p1_id, p2_id, p1_score, p2_score, token) -> dict` — build + submit.

- [ ] **Step 1: Write the failing tests**

Append before the `if __name__` block:

```python
class ReportSetTests(unittest.TestCase):
    def test_calls_graphql_with_mutation_and_variables(self):
        with mock.patch("startgg._graphql",
                        return_value={"reportBracketSet": [{"id": 99}]}) as g:
            report_set(99, 10, 20, 3, 1, "tok")
        args = g.call_args[0]
        self.assertEqual(args[0], startgg.REPORT_SET_MUTATION)
        self.assertEqual(args[1]["winnerId"], 10)
        self.assertEqual(args[1]["setId"], 99)
        self.assertEqual(args[2], "tok")

    def test_propagates_graphql_errors(self):
        with mock.patch("startgg._graphql", side_effect=StreamQueueError("nope")):
            with self.assertRaises(StreamQueueError):
                report_set(99, 10, 20, 3, 1, "tok")

    def test_validation_error_before_call(self):
        with mock.patch("startgg._graphql") as g:
            with self.assertRaises(StreamQueueError):
                report_set(99, 10, 20, 2, 2, "tok")  # tie
        g.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 tests/test_startgg.py -v`
Expected: FAIL — `AttributeError: module 'startgg' has no attribute 'REPORT_SET_MUTATION'` (or the stub `report_set` from Task 2 lacks `submit_report`).

- [ ] **Step 3: Add the mutation and functions**

In `startgg.py`, add after `build_report_variables`:

```python
REPORT_SET_MUTATION = """
mutation ReportSet($setId: ID!, $winnerId: ID!, $gameData: [BracketSetGameDataInput]) {
  reportBracketSet(setId: $setId, winnerId: $winnerId, gameData: $gameData) {
    id
    state
  }
}
""".strip()


def submit_report(variables, token):
    """Run the reportBracketSet mutation with pre-built variables."""
    return _graphql(REPORT_SET_MUTATION, variables, token)


def report_set(set_id, p1_id, p2_id, p1_score, p2_score, token):
    """Build variables from the panel score and report the set."""
    return submit_report(
        build_report_variables(set_id, p1_id, p2_id, p1_score, p2_score), token)
```

(If you added the `report_set` stub in Task 2, replace it with this final version — keep only one definition.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add startgg.py tests/test_startgg.py
git commit -m "feat: report set result via reportBracketSet mutation"
```

---

### Task 4: Server `/api/streamqueue/report` endpoint

**Files:**
- Modify: `server.py`

**Interfaces:**
- Consumes: `startgg.build_report_variables`, `startgg.submit_report`, `startgg.StreamQueueError`.
- Produces: `POST /api/streamqueue/report` `{setId, p1Id, p2Id, p1Score, p2Score}` → `403` no token, `400` invalid, `502` start.gg error, `200 {ok:true}`.

- [ ] **Step 1: Add the route**

In `do_POST`, add before the final `else`:

```python
        elif self.path == "/api/streamqueue/report":
            self._handle_post_report()
```

- [ ] **Step 2: Add the handler**

Add after `_handle_post_station`:

```python
    def _handle_post_report(self):
        if not self._require_token():
            return
        try:
            body = self._read_json_body()
        except ValueError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        try:
            variables = startgg.build_report_variables(
                body.get("setId"), body.get("p1Id"), body.get("p2Id"),
                body.get("p1Score"), body.get("p2Score"))
        except startgg.StreamQueueError as e:
            self.send_json(400, {"error": str(e)})
            return
        try:
            startgg.submit_report(variables, STARTGG_TOKEN)
            self.send_json(200, {"ok": True})
        except startgg.StreamQueueError as e:
            self.send_json(502, {"error": str(e)})
```

- [ ] **Step 3: Smoke test the three gates**

Run (fake token so the feature is enabled; the real API call fails on the fake token, which is expected for the valid-scores case):

```bash
SP="/private/tmp/claude-501/-Users-renatomrcosta-repos-fgc-scoreboard/5c9af3c3-0afc-45a0-8cf6-112af1a31497/scratchpad"
STARTGG_TOKEN=faketoken PORT=8094 python3 server.py & SRV=$!
sleep 1
printf '{"setId":1,"p1Id":10,"p2Id":20,"p1Score":2,"p2Score":2}' > "$SP/tie.json"
printf '{"setId":1,"p1Id":10,"p2Id":20,"p1Score":3,"p2Score":1}' > "$SP/win.json"
echo "tie -> $(curl -s -X POST localhost:8094/api/streamqueue/report --data-binary @"$SP/tie.json" -w ' [%{http_code}]')"
echo "valid(fake tok) -> $(curl -s -X POST localhost:8094/api/streamqueue/report --data-binary @"$SP/win.json" -w ' [%{http_code}]')"
kill $SRV
# no-token gate:
STARTGG_TOKEN= PORT=8095 python3 server.py & SRV=$!
sleep 1
echo "no token -> $(curl -s -X POST localhost:8095/api/streamqueue/report --data-binary @"$SP/win.json" -w ' [%{http_code}]')"
kill $SRV
```

Expected: `tie -> {"error":"Cannot report a tie"} [400]`; `valid(fake tok) -> {"error":"start.gg HTTP ..."} [502]`; `no token -> {"error":"Stream queue disabled ..."} [403]`.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add /api/streamqueue/report endpoint"
```

---

### Task 5: `control.html` — sponsor autofill + Report button

**Files:**
- Modify: `control.html`

**Interfaces:**
- Consumes: `/api/streamqueue/report`; normalized set fields `setId`, `p1Id`, `p2Id`, `p1Team`, `p2Team`.
- Reuses: `state`, `renderAll()`, `setDirty()`, `showStatus()`, `readFields()`, `queueConfig`, `refreshQueue()`.

- [ ] **Step 1: Populate the sponsor fields on load**

Replace the body of `loadMatch` with (fills teams from the queue, still clears 2XKO 2nd-player fields, and sets `activeSet`):

```javascript
    function loadMatch(set) {
      state.p1Name = set.p1 || "";
      state.p2Name = set.p2 || "";
      state.round  = set.fullRoundText || "";
      state.p1Score = 0;
      state.p2Score = 0;
      state.p1Team = set.p1Team || "";
      state.p2Team = set.p2Team || "";
      // Clear 2XKO 2nd-player fields — the queue doesn't provide them
      state.p1Name2 = ""; state.p1Team2 = "";
      state.p2Name2 = ""; state.p2Team2 = "";
      activeSet = (set.setId && set.p1Id != null && set.p2Id != null)
        ? { setId: set.setId, p1Id: set.p1Id, p2Id: set.p2Id } : null;
      renderAll();
      setDirty(true);
    }
```

- [ ] **Step 2: Add the Report button CSS**

In the `<style>` block, after the `.queue-empty` rule, add:

```css
    .btn-report {
      background: #6d3aad; color: #fff; font-size: 0.9rem;
      padding: 0.6rem 1.1rem; font-weight: 600;
    }
    .btn-report:hover:not(:disabled) { background: #7d47c4; }
    .btn-report:disabled { opacity: 0.4; cursor: not-allowed; }
```

- [ ] **Step 3: Add the Report button to the actions row**

In the `.actions` div, add the button just before the Submit button:

```html
      <button class="btn-report" id="reportBtn" onclick="reportSet()"
              style="display:none;" disabled>Report to start.gg</button>
```

So the row reads: status span, dirty dot, Swap, **Report**, Submit.

- [ ] **Step 4: Track and swap the active set; wire the report call**

In the Stream Queue JS section, change the state declaration line:

```javascript
    let queueTimer = null;
```

to add `activeSet`:

```javascript
    let queueTimer = null;
    let activeSet = null;   // {setId, p1Id, p2Id} for the loaded queue match
```

Add `updateReportButton` and `reportSet` (place them next to `loadMatch`):

```javascript
    function updateReportButton() {
      const btn = document.getElementById("reportBtn");
      if (!btn) return;
      btn.style.display = queueConfig.enabled ? "" : "none";
      btn.disabled = !activeSet;
    }

    async function reportSet() {
      if (!activeSet) return;
      readFields();
      const p1 = state.p1Score, p2 = state.p2Score;
      if (p1 === p2) { showStatus("Can't report: scores are tied.", "err"); return; }
      if (p1 === 0 && p2 === 0) { showStatus("Can't report: no score yet.", "err"); return; }
      const msg = `Report ${state.p1Name || "P1"} ${p1}–${p2} `
        + `${state.p2Name || "P2"} to start.gg?`;
      if (!confirm(msg)) return;
      try {
        const res = await fetch("/api/streamqueue/report", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            setId: activeSet.setId, p1Id: activeSet.p1Id, p2Id: activeSet.p2Id,
            p1Score: p1, p2Score: p2,
          }),
        });
        const data = await res.json();
        if (!res.ok) { showStatus("Report failed: " + (data.error || res.status), "err"); return; }
        showStatus("Reported to start.gg.", "ok");
        activeSet = null;
        updateReportButton();
        refreshQueue();
      } catch (e) {
        showStatus("Report failed: " + e.message, "err");
      }
    }
```

- [ ] **Step 5: Keep the button state in sync**

At the end of `renderAll()` (after `toggle2XKO();`), add:

```javascript
      updateReportButton();
```

In `swapPlayers()`, after the score-swap line and before `renderAll();`, add the ID swap:

```javascript
      if (activeSet) {
        [activeSet.p1Id, activeSet.p2Id] = [activeSet.p2Id, activeSet.p1Id];
      }
```

After the existing dirty-tracking listener block (the `.forEach(id => ... addEventListener("input", () => setDirty(true)))`), add a listener that clears the binding on name edits:

```javascript
    // Editing a player name unbinds the loaded bracket set (disables Report)
    ["p1Name", "p2Name"].forEach(id => {
      document.getElementById(id).addEventListener("input", () => {
        activeSet = null;
        updateReportButton();
      });
    });
```

In `initQueue()`, after setting `document.getElementById("queueSection").style.display = "flex";`, add:

```javascript
      updateReportButton();
```

- [ ] **Step 6: Verify JS syntax**

Run:

```bash
cd /Users/renatomrcosta/repos/fgc-scoreboard
python3 - <<'EOF'
import re
html = open("control.html").read()
m = re.search(r"<script>(.*)</script>", html, re.S)
open("/tmp/_ctrl.js","w").write(m.group(1))
EOF
node --check /tmp/_ctrl.js && echo "JS syntax OK"
```

Expected: `JS syntax OK`.

- [ ] **Step 7: Commit**

```bash
git add control.html
git commit -m "feat: report button and queue sponsor autofill in control panel"
```

---

### Task 6: Docs + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document reporting in the stream queue README section**

In `README.md`, at the end of the "Start.gg Stream Queue" section (after the paragraph ending "…via the **Refresh** button."), add:

```markdown

Once a match is loaded from the queue, set the final score and click **Report to
start.gg** to submit the result to the bracket (the winner is the higher score).
You'll be asked to confirm first, and the queue refreshes afterward. The button
is only active for a match loaded from the queue; editing a player's name
unbinds it (click the card again to re-enable). Sponsor tags from start.gg are
filled into the Team/Sponsor field automatically.
```

- [ ] **Step 2: Full test run**

Run: `python3 tests/test_startgg.py -v`
Expected: PASS (all classes).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document set reporting and sponsor autofill"
```

---

## Notes for the implementer

- Run `python3 tests/test_startgg.py -v` after each Python task.
- Full end-to-end reporting requires a real `STARTGG_TOKEN`, a loaded queue match,
  and TO/admin permissions on that tournament; the smoke tests only cover the
  gates (403/400/502), which need no valid token.
- Tasks 2 and 3 are split for review clarity but must land together (Task 2's
  import line references `report_set`); commit them back-to-back.
