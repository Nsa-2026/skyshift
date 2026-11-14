#!/usr/bin/env python3
"""Disposable local UI + same-origin read-only proxy. Python stdlib only."""
import argparse
import json
import mimetypes
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def safe_file(base, name):
    path = (base / unquote(name)).resolve()
    return path if path.is_relative_to(base.resolve()) and path.is_file() else None


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def error_json(self, status, message):
        self.send(status, json.dumps({"error": {"code": "playground", "message": message}}).encode())

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/_config":
            return self.send(200, json.dumps({"api_origin": self.server.api_origin,
                "static_ref": self.server.static_ref or "working tree"}).encode())
        if path.startswith("/api/"):
            # Fixed upstream, GET only; the browser cannot choose arbitrary proxy hosts.
            suffix = path + ("?" + parsed.query if parsed.query else "")
            try:
                req = Request(self.server.api_origin + suffix, headers={"Accept": "application/json, application/octet-stream"})
                with urlopen(req, timeout=180) as r:
                    return self.send(r.status, r.read(), r.headers.get("Content-Type", "application/octet-stream"))
            except HTTPError as e:
                return self.send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
            except (URLError, TimeoutError, OSError) as e:
                return self.error_json(502, f"Backend unavailable at {self.server.api_origin}: {e}. Start the API or choose Fixtures.")
        if path.startswith("/data/") and self.server.static_ref:
            name = unquote(path[len("/data/"):])
            if any(x in ("", ".", "..") for x in name.split("/")) or "\\" in name:
                return self.error_json(400, "Invalid data path")
            try:
                result = subprocess.run(["git", "show", f"{self.server.static_ref}:web/public/data/{name}"],
                    cwd=REPO, capture_output=True, timeout=10)
                if result.returncode == 0:
                    return self.send(200, result.stdout, mimetypes.guess_type(name)[0] or "application/octet-stream")
            except (OSError, subprocess.TimeoutExpired):
                return self.error_json(503, "Could not read static data from the selected Git ref")
            return self.error_json(404, "No static file at selected Git ref")
        if path.startswith("/fixtures/"):
            base, name = REPO / "fixtures" / "api", path[len("/fixtures/"):]
        elif path.startswith("/data/"):
            base, name = REPO / "web" / "public" / "data", path[len("/data/"):]
        else:
            base, name = HERE, (path.lstrip("/") or "index.html")
            if name not in {"index.html", "app.mjs", "model.mjs", "style.css"}:
                return self.error_json(404, "No such UI asset")
        f = safe_file(base, name)
        if not f:
            return self.error_json(404, "No such file")
        self.send(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "application/octet-stream")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4173)
    ap.add_argument("--api-origin", default="http://127.0.0.1:8081")
    ap.add_argument("--static-ref", help="Read static /data from a fetched Git ref, without checking it out")
    args = ap.parse_args()
    origin = urlsplit(args.api_origin)
    if origin.scheme not in ("http", "https") or not origin.netloc or origin.path not in ("", "/") or origin.query or origin.fragment or origin.username:
        ap.error("--api-origin must be an HTTP(S) origin, e.g. http://127.0.0.1:8081")
    if args.static_ref:
        check = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", args.static_ref + "^{commit}"],
            cwd=REPO, capture_output=True, text=True)
        if check.returncode:
            ap.error("--static-ref must name a fetched commit or branch")
        args.static_ref = check.stdout.strip()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.api_origin = args.api_origin.rstrip("/")
    server.static_ref = args.static_ref
    print(f"SkyShift Playground: http://127.0.0.1:{args.port}  API: {server.api_origin}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
