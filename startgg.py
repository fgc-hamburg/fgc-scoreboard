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

            def name_at(i):
                if i < len(slots):
                    ent = slots[i].get("entrant")
                    if ent:
                        return ent.get("name")
                return None

            sets_out.append({
                "fullRoundText": s.get("fullRoundText"),
                "round": s.get("round"),
                "p1": name_at(0),
                "p2": name_at(1),
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
        fullRoundText
        round
        slots { entrant { name } }
      }
    }
  }
}
""".strip()


class StreamQueueError(Exception):
    """Raised when the start.gg stream queue cannot be fetched."""


def _post_graphql(slug, token):
    payload = json.dumps({
        "query": STREAM_QUEUE_QUERY,
        "variables": {"tourneySlug": slug},
    }).encode()
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
    return normalize_stream_queue(_post_graphql(slug, token))


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
