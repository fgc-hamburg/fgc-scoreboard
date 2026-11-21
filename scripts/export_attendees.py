#!/usr/bin/env python3
"""Export every attendee of a start.gg tournament to CSV, with entry fees.

One row per attendee: gamertag, real name, the events they registered for,
whether they got the early-bird rate, and the entry fee that follows from it.

Fees (defaults, override with --rate-*):
    early bird   20 EUR per game
    standard     30 EUR per game
    spectator     5 EUR flat (attendee registered for no games)

Early bird cannot come from the start.gg API: the GraphQL schema has no ticket,
admission or registration-date field anywhere (registering is modelled as a bare
list of event ids). Pass --registrations with the CSV the start.gg admin
dashboard exports and the tier is read from its ticket name, or from its
registration date against --early-bird-until. Without that file every player is
billed at the standard rate and flagged early_bird=unknown.

Standard library only, same as the rest of this repo.

Usage:
    python3 scripts/export_attendees.py <tournament-slug-or-url> [-o out.csv]
"""

import argparse
import csv
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

API_URL = "https://api.start.gg/gql/alpha"
PER_PAGE = 50

QUERY = """
query Attendees($slug: String!, $page: Int!, $perPage: Int!) {
  tournament(slug: $slug) {
    id
    name
    participants(query: {page: $page, perPage: $perPage}) {
      pageInfo { total totalPages }
      nodes {
        id
        gamerTag
        prefix
        email
        checkedIn
        contactInfo { name nameFirst nameLast }
        user { id name }
        events { id name }
      }
    }
  }
}
"""

_SLUG_RE = re.compile(r"/tournament/([^/?#]+)")


def parse_slug(value):
    """Accept a full start.gg URL or a bare slug and return the bare slug."""
    value = (value or "").strip()
    m = _SLUG_RE.search(value)
    if m:
        return m.group(1)
    return value.strip("/") or None


def read_token():
    """Token from STARTGG_TOKEN, else from the STARTGG_TOKEN line in .env."""
    token = os.environ.get("STARTGG_TOKEN")
    if token:
        return token.strip()
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("STARTGG_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def gql(token, variables):
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise SystemExit("start.gg HTTP %s: %s" % (exc.code, exc.read().decode()[:500]))
    except urllib.error.URLError as exc:
        raise SystemExit("start.gg request failed: %s" % exc.reason)
    if payload.get("errors"):
        raise SystemExit("start.gg GraphQL error: %s" % json.dumps(payload["errors"]))
    return payload.get("data") or {}


def fetch_participants(token, slug):
    """Page through every participant. Returns (tournament_name, nodes)."""
    page, total_pages, name, nodes = 1, 1, slug, []
    while page <= total_pages:
        data = gql(token, {"slug": slug, "page": page, "perPage": PER_PAGE})
        tournament = data.get("tournament")
        if not tournament:
            raise SystemExit("No tournament found for slug %r" % slug)
        name = tournament.get("name") or slug
        participants = tournament.get("participants") or {}
        total_pages = (participants.get("pageInfo") or {}).get("totalPages") or 1
        nodes.extend(participants.get("nodes") or [])
        sys.stderr.write("fetched page %d/%d\n" % (page, total_pages))
        page += 1
    return name, nodes


def real_name(node):
    contact = node.get("contactInfo") or {}
    joined = " ".join(p for p in (contact.get("nameFirst"), contact.get("nameLast")) if p).strip()
    return contact.get("name") or joined or (node.get("user") or {}).get("name") or ""


# --- registration export (optional) ------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
)


def parse_date(value):
    """Best-effort date parse of a start.gg export cell. None when unparseable."""
    value = (value or "").strip().replace("Z", "")
    if not value:
        return None
    if value.isdigit():  # unix timestamp
        return datetime.date.fromtimestamp(int(value))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value[:19], fmt).date()
        except ValueError:
            continue
    return None


def load_registrations(path):
    """Map lowercased gamertag/email -> {"ticket": str, "date": date|None}.

    Reads the CSV exported from the start.gg admin dashboard. Column names vary
    between exports, so they are matched case-insensitively against a few known
    spellings.
    """
    if not path:
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = [f for f in (reader.fieldnames or []) if f]

        def pick(*candidates):
            lowered = {f.strip().lower(): f for f in fields}
            for want in candidates:
                if want in lowered:
                    return lowered[want]
            return None

        tag_col = pick("gamertag", "gamer tag", "tag", "attendee", "name")
        email_col = pick("email", "email address", "e-mail")
        ticket_col = pick("ticket", "ticket name", "ticket type", "registration type", "admission")
        date_col = pick(
            "registered at", "registration date", "date registered", "created at",
            "registered on", "purchase date", "date",
        )
        if not (ticket_col or date_col):
            raise SystemExit(
                "%s has neither a ticket column nor a registration date column.\n"
                "Columns found: %s" % (path, ", ".join(fields))
            )
        if not (tag_col or email_col):
            raise SystemExit(
                "%s has no gamertag or email column to join on.\nColumns found: %s"
                % (path, ", ".join(fields))
            )
        for row in reader:
            entry = {
                "ticket": (row.get(ticket_col) or "").strip() if ticket_col else "",
                "date": parse_date(row.get(date_col)) if date_col else None,
            }
            if not entry["ticket"] and not entry["date"]:
                continue
            for col in (tag_col, email_col):
                key = (row.get(col) or "").strip().lower() if col else ""
                if key:
                    out[key] = entry
    return out


# --- fee logic ----------------------------------------------------------------

def classify(events, registration, cutoff):
    """Return (attendee_type, early_bird, basis, registered_at).

    early_bird is "yes"/"no"/"" (empty = not applicable, i.e. spectator).
    basis records where the answer came from, so the CSV stays auditable.
    """
    ticket = (registration or {}).get("ticket") or ""
    date = (registration or {}).get("date")
    stamp = date.isoformat() if date else ""

    if ticket:
        low = ticket.lower()
        if "spectat" in low:
            return "spectator", "", "ticket:%s" % ticket, stamp
        if "early" in low:
            return "player", "yes", "ticket:%s" % ticket, stamp
        return "player", "no", "ticket:%s" % ticket, stamp

    if not events:
        return "spectator", "", "no events registered", stamp

    if date:
        return "player", ("yes" if date <= cutoff else "no"), "registered %s" % stamp, stamp

    return "player", "unknown", "no registration data - billed at standard rate", stamp


def fee_for(attendee_type, early_bird, num_events, rates):
    if attendee_type == "spectator":
        return rates["spectator"]
    rate = rates["early"] if early_bird == "yes" else rates["standard"]
    return rate * num_events


FIELDS = [
    "gamertag",
    "prefix",
    "real_name",
    "email",
    "attendee_type",
    "early_bird",
    "registered_at",
    "num_events",
    "events",
    "entry_fee_eur",
    "fee_basis",
    "checked_in",
    "participant_id",
    "user_id",
]


def build_rows(nodes, registrations, cutoff, rates):
    rows = []
    for node in nodes:
        events = sorted((e.get("name") or "") for e in (node.get("events") or []))
        tag = (node.get("gamerTag") or "").strip()
        email = (node.get("email") or "").strip()
        registration = registrations.get(tag.lower()) or registrations.get(email.lower())
        attendee_type, early_bird, basis, stamp = classify(events, registration, cutoff)
        rows.append(
            {
                "gamertag": tag,
                "prefix": node.get("prefix") or "",
                "real_name": real_name(node),
                "email": email,
                "attendee_type": attendee_type,
                "early_bird": early_bird,
                "registered_at": stamp,
                "num_events": len(events),
                "events": ", ".join(events),
                "entry_fee_eur": fee_for(attendee_type, early_bird, len(events), rates),
                "fee_basis": basis,
                "checked_in": "" if node.get("checkedIn") is None else str(bool(node["checkedIn"])).lower(),
                "participant_id": node.get("id") or "",
                "user_id": (node.get("user") or {}).get("id") or "",
            }
        )
    rows.sort(key=lambda r: r["gamertag"].lower())
    return rows


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("tournament", help="start.gg tournament slug or URL")
    parser.add_argument("-o", "--out", help="output CSV path (default: attendees-<slug>.csv)")
    parser.add_argument(
        "--registrations",
        help="start.gg admin registration export CSV, for ticket tier / registration date",
    )
    parser.add_argument(
        "--early-bird-until",
        default="2026-08-15",
        help="last day (inclusive) that counts as early bird (default: %(default)s)",
    )
    parser.add_argument("--rate-early", type=float, default=20, help="EUR per game, early bird")
    parser.add_argument("--rate-standard", type=float, default=30, help="EUR per game, standard")
    parser.add_argument("--rate-spectator", type=float, default=5, help="EUR flat, spectator")
    args = parser.parse_args()

    slug = parse_slug(args.tournament)
    if not slug:
        raise SystemExit("Could not parse a tournament slug from %r" % args.tournament)

    cutoff = parse_date(args.early_bird_until)
    if not cutoff:
        raise SystemExit("Could not parse --early-bird-until %r (use YYYY-MM-DD)" % args.early_bird_until)

    token = read_token()
    if not token:
        raise SystemExit("No start.gg token. Set STARTGG_TOKEN or add it to .env")

    rates = {
        "early": args.rate_early,
        "standard": args.rate_standard,
        "spectator": args.rate_spectator,
    }

    registrations = load_registrations(args.registrations)
    name, nodes = fetch_participants(token, slug)
    rows = build_rows(nodes, registrations, cutoff, rates)

    out_path = args.out or "attendees-%s.csv" % slug
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(r["entry_fee_eur"] for r in rows)
    unknown = sum(1 for r in rows if r["early_bird"] == "unknown")
    sys.stderr.write("%s: wrote %d attendees to %s\n" % (name, len(rows), out_path))
    sys.stderr.write("total entry fees: %.2f EUR\n" % total)
    if unknown:
        sys.stderr.write(
            "warning: %d attendees have no ticket/date data and were billed at the "
            "standard rate (pass --registrations)\n" % unknown
        )


if __name__ == "__main__":
    main()
