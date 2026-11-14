"""Serve the Compare preview, and proxy /api to the backend.

    .venv\\Scripts\\python compare_preview/serve.py          # http://127.0.0.1:5174
    .venv\\Scripts\\python compare_preview/serve.py --port 5900 --api http://127.0.0.1:8000

Same design as ``search_preview/serve.py``, and deliberately a separate file
rather than an import of it: ``search_preview/`` is a working tool that the
Library & Search work is validated against, and the brief says not to
destabilise it. Copying 130 lines of standard-library HTTP for a throwaway
development tool is cheaper than coupling two throwaway tools together.

**One real difference: this proxy forwards POST as well as GET.**
``POST /api/compare`` is how a comparison is run, so a GET-only proxy could
not serve this page at all. The forwarding is still narrow: POST is allowed
only to paths under ``/api/``, only to a loopback backend, and only with a
body small enough to be a comparison request.

Standard library only -- ``http.server`` plus ``urllib`` -- so it adds no
dependency to ``requirements.txt``.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Only loopback backends. A dev proxy that will forward anywhere is an open
#: relay, and this one is started by hand on a student laptop.
ALLOWED_BACKEND_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

#: A comparison request is a small JSON object. The cap is here so a stray
#: large POST cannot be relayed through this tool.
MAX_BODY_BYTES = 64 * 1024


class PreviewHandler(SimpleHTTPRequestHandler):
    """Static files from this directory; ``/api/*`` proxied to the backend."""

    api_base = "http://127.0.0.1:8000"

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/api/"):
            self.send_error(404, "POST is proxied for /api/ only")
            return
        self._proxy("POST")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        if length > MAX_BODY_BYTES:
            raise ValueError(
                f"request body is {length} bytes; this development proxy "
                f"forwards at most {MAX_BODY_BYTES}"
            )
        return self.rfile.read(length)

    def _proxy(self, method: str) -> None:
        target = f"{self.api_base}{self.path}"
        try:
            body = self._read_body() if method == "POST" else None
        except ValueError as exc:
            self._respond(
                413,
                '{"error":{"code":"FILE_TOO_LARGE","message":"'
                + str(exc).replace('"', "'")
                + '","details":{}}}',
            )
            return

        request = urllib.request.Request(target, data=body, method=method)
        if method == "POST":
            request.add_header(
                "Content-Type",
                self.headers.get("Content-Type", "application/json"),
            )
        try:
            with urllib.request.urlopen(request, timeout=120) as upstream:
                payload = upstream.read()
                status = upstream.status
                content_type = upstream.headers.get(
                    "Content-Type", "application/json"
                )
        except urllib.error.HTTPError as exc:
            # The API's error envelope is part of its contract: pass the body
            # through unchanged so the page can show the real code and message.
            payload = exc.read()
            status = exc.code
            content_type = exc.headers.get("Content-Type", "application/json")
        except urllib.error.URLError as exc:
            payload = (
                b'{"error":{"code":"BACKEND_UNREACHABLE","message":'
                b'"the preview server could not reach the API: '
                + str(exc.reason).encode("utf-8", "replace")
                + b'","details":{}}}'
            )
            status = 502
            content_type = "application/json"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # A preview must never show a cached result while someone is changing
        # the version pair: a stale diff looks exactly like a wrong diff.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _respond(self, status: int, text: str) -> None:
        encoded = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Compare preview.")
    # 5174, one past the search preview's 5173, so both can run at once --
    # which is how the "did Compare break search?" question gets answered.
    parser.add_argument("--port", type=int, default=5174)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--api",
        default="http://127.0.0.1:8000",
        help="Backend base URL (loopback only).",
    )
    args = parser.parse_args(argv)

    host = urllib.parse.urlparse(args.api).hostname
    if host not in ALLOWED_BACKEND_HOSTS:
        print(
            f"refusing to proxy to {host!r}: this development proxy forwards "
            f"to loopback only ({sorted(ALLOWED_BACKEND_HOSTS)})."
        )
        return 2

    PreviewHandler.api_base = args.api.rstrip("/")
    handler = partial(PreviewHandler, directory=str(HERE))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"NiyamSetu Compare preview: http://{args.host}:{args.port}\n"
        f"proxying /api (GET and POST)  ->  {PreviewHandler.api_base}\n"
        "Ctrl+C to stop."
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
