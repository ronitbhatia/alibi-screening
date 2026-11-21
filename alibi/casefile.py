"""Serve the analyst case file and its two local data files on loopback."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .batch import trace_path

UI = Path(__file__).resolve().parents[1] / "ui"


def handler(verdicts):
    routes = {
        "/": (UI / "index.html", "text/html"),
        "/app.js": (UI / "app.js", "text/javascript"),
        "/style.css": (UI / "style.css", "text/css"),
        "/data/verdicts": (verdicts, "application/x-ndjson"),
        "/data/traces": (trace_path(verdicts), "application/x-ndjson"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            item = routes.get(urlsplit(self.path).path)
            if item is None or not item[0].is_file():
                self.send_error(404)
                return
            body = item[0].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", item[1] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verdicts", nargs="?", type=Path, default=Path("output/dev.jsonl"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler(args.verdicts)) as server:
        print(f"ALIBI case file: http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
