"""A receiver for Alertmanager webhooks: appends each delivery to a JSON-lines file and prints one line per alert.

    python scripts/alert-sink.py --port 9094 --output artifacts/alerts-received.jsonl

It is the place alerts are delivered to when checking the routing locally. It does nothing else: no mail, no chat.
"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path


def make_handler(output):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            record = {"received_at": datetime.now(timezone.utc).isoformat(), "path": self.path, "payload": body}
            with output.open("a") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            for alert in body.get("alerts", []):
                print(f"{record['received_at']} {self.path} {alert.get('status')} {alert['labels'].get('alertname')}", flush=True)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9094)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--output", default="artifacts/alerts-received.jsonl")
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    HTTPServer((args.host, args.port), make_handler(target)).serve_forever()
