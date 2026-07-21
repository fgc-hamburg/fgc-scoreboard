"""Domain logic for the start.gg stream queue integration.

Standard-library only. All start.gg network access happens here so the bearer
token never reaches the browser.
"""

import json
import re
import urllib.error
import urllib.request

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


def _slot(slots, i):
    """Return (entrant_id, name, team) for slot i, or (None, None, "").

    Single-participant entrants (singles) split into a clean gamerTag name and a
    sponsor prefix; teams/duos (0 or 2+ participants) keep the entrant name.
    """
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


API_URL = "https://api.start.gg/gql/alpha"

STREAM_QUEUE_QUERY = """
query StreamQueueOnTournament($tourneySlug: String!) {
  tournament(slug: $tourneySlug) {
    streamQueue {
      stream { streamName }
      sets {
        id
        fullRoundText
        round
        slots { entrant { id name participants { prefix gamerTag } } }
      }
    }
  }
}
""".strip()


class StreamQueueError(Exception):
    """Raised when the start.gg stream queue cannot be fetched."""


def _graphql(query, variables, token):
    """POST a GraphQL query/mutation to start.gg and return its `data` object.

    Raises StreamQueueError on HTTP, network, JSON, or GraphQL errors.
    """
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


def fetch_stream_queue(slug, token):
    """Fetch and normalize the stream queue for a tournament slug."""
    return normalize_stream_queue(
        _graphql(STREAM_QUEUE_QUERY, {"tourneySlug": slug}, token))


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
