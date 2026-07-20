#!/usr/bin/env python3
"""
FGC Scoreboard Control Dashboard server.
Serves the control panel UI and provides API endpoints to read/write
sc/streamcontrol.json.

Usage:
    python3 server.py
    open http://localhost:8080

Environment variables:
    PORT  HTTP port to listen on (default: 8080)
"""

import csv
import json
import os
import re
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).parent
JSON_PATH = BASE_DIR / "sc" / "streamcontrol.json"
ROUND_CSV_PATH = BASE_DIR / "sc" / "round.csv"
CONTROL_HTML_PATH = BASE_DIR / "control.html"

PORT = int(os.environ.get("PORT", 8080))

EVENT_CSV_HEADER = [
    "timestamp", "event_type", "event_name", "game", "round",
    "p1_name", "p2_name", "p1_score", "p2_score", "delta",
]


def slugify(s):
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "untitled"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._serve_file(CONTROL_HTML_PATH, "text/html")
        elif self.path == "/api/data":
            self._handle_get_data()
        elif self.path == "/api/rounds":
            self._handle_get_rounds()
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/api/update":
            self._handle_post_update()
        elif self.path == "/api/log-event":
            self._handle_log_event()
        else:
            self.send_json(404, {"error": "Not found"})

    def _serve_file(self, path, content_type):
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_json(404, {"error": f"{path.name} not found"})

    def _handle_get_data(self):
        try:
            data = json.loads(JSON_PATH.read_text())
            self.send_json(200, data)
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def _handle_get_rounds(self):
        try:
            rounds = [
                line.strip()
                for line in ROUND_CSV_PATH.read_text().splitlines()
                if line.strip()
            ]
            self.send_json(200, rounds)
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def _handle_post_update(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            data["timestamp"] = str(int(time.time()))
            JSON_PATH.write_text(json.dumps(data, indent=4))
            self.send_json(200, {"ok": True})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def _handle_log_event(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            payload = json.loads(body)

            ts = datetime.now().astimezone().isoformat(timespec="seconds")

            event_name = (payload.get("event_name") or "").strip()
            event_date = (payload.get("event_date") or "").strip()
            if not event_date:
                event_date = datetime.now().strftime("%Y-%m-%d")
            filename = f"events-{slugify(event_name)}-{event_date}.csv"
            path = BASE_DIR / filename

            row = {
                "timestamp": ts,
                "event_type": payload.get("event_type", ""),
                "event_name": event_name,
                "game": payload.get("game", ""),
                "round": payload.get("round", ""),
                "p1_name": payload.get("p1_name", ""),
                "p2_name": payload.get("p2_name", ""),
                "p1_score": payload.get("p1_score", ""),
                "p2_score": payload.get("p2_score", ""),
                "delta": payload.get("delta", ""),
            }

            write_header = not path.exists()
            with path.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=EVENT_CSV_HEADER)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)

            self.send_json(200, {"ok": True, "file": filename})
        except Exception as e:
            self.send_json(500, {"error": str(e)})


if __name__ == "__main__":
    server = HTTPServer(("", PORT), Handler)
    print(f"Control dashboard running at http://localhost:{PORT}")
    print(f"  JSON file: {JSON_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
