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

import startgg

BASE_DIR = Path(__file__).parent


def _load_env_file(path):
    """Minimal .env loader (KEY=VALUE lines). Real env vars take precedence.

    Avoids a python-dotenv dependency so `python3 server.py` works on system
    python3 (see Makefile).
    """
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")
JSON_PATH = BASE_DIR / "sc" / "streamcontrol.json"
ROUND_CSV_PATH = BASE_DIR / "sc" / "round.csv"
CONTROL_HTML_PATH = BASE_DIR / "control.html"

PORT = int(os.environ.get("PORT", 8080))
QUEUE_CONFIG_PATH = BASE_DIR / "sc" / "streamqueue.json"
STARTGG_TOKEN = os.environ.get("STARTGG_TOKEN", "").strip()
STREAM_QUEUE_REFRESH_SECONDS = int(os.environ.get("STREAM_QUEUE_REFRESH_SECONDS", "30"))

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
        elif self.path == "/api/streamqueue/config":
            self._handle_streamqueue_config()
        elif self.path == "/api/streamqueue":
            self._handle_get_streamqueue()
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/api/update":
            self._handle_post_update()
        elif self.path == "/api/log-event":
            self._handle_log_event()
        elif self.path == "/api/streamqueue/tournament":
            self._handle_post_tournament()
        elif self.path == "/api/streamqueue/station":
            self._handle_post_station()
        elif self.path == "/api/streamqueue/report":
            self._handle_post_report()
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

    # ---------- Stream queue ----------

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _require_token(self):
        if not STARTGG_TOKEN:
            self.send_json(403, {"error": "Stream queue disabled (no STARTGG_TOKEN)"})
            return False
        return True

    def _handle_streamqueue_config(self):
        cfg = startgg.load_queue_config(QUEUE_CONFIG_PATH)
        self.send_json(200, startgg.build_config_response(
            STARTGG_TOKEN, STREAM_QUEUE_REFRESH_SECONDS, cfg))

    def _handle_get_streamqueue(self):
        if not self._require_token():
            return
        cfg = startgg.load_queue_config(QUEUE_CONFIG_PATH)
        if not cfg.get("slug"):
            self.send_json(409, {"error": "No tournament loaded"})
            return
        try:
            stations = startgg.fetch_stream_queue(cfg["slug"], STARTGG_TOKEN)
            self.send_json(200, {"stations": stations, "streamName": cfg.get("streamName")})
        except startgg.StreamQueueError as e:
            self.send_json(502, {"error": str(e)})

    def _handle_post_tournament(self):
        if not self._require_token():
            return
        try:
            body = self._read_json_body()
        except ValueError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        slug = startgg.parse_slug(body.get("url", ""))
        if not slug:
            self.send_json(400, {"error": "Could not parse tournament URL"})
            return
        startgg.save_queue_config(QUEUE_CONFIG_PATH, {"slug": slug, "streamName": None})
        try:
            stations = startgg.fetch_stream_queue(slug, STARTGG_TOKEN)
            self.send_json(200, {"slug": slug, "stations": stations, "streamName": None})
        except startgg.StreamQueueError as e:
            self.send_json(502, {"error": str(e)})

    def _handle_post_station(self):
        if not self._require_token():
            return
        try:
            body = self._read_json_body()
        except ValueError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        cfg = startgg.load_queue_config(QUEUE_CONFIG_PATH)
        cfg["streamName"] = body.get("streamName")
        startgg.save_queue_config(QUEUE_CONFIG_PATH, cfg)
        self.send_json(200, {"ok": True, "streamName": cfg["streamName"]})

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


if __name__ == "__main__":
    server = HTTPServer(("", PORT), Handler)
    print(f"Control dashboard running at http://localhost:{PORT}")
    print(f"  JSON file: {JSON_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
