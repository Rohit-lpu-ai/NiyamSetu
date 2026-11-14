"""Serve the Library & Search preview, and proxy /api to the backend.

    .venv\\Scripts\\python search_preview/serve.py          # http://127.0.0.1:5173
    .venv\\Scripts\\python search_preview/serve.py --port 5800 --api http://127.0.0.1:8000

Why a 60-line server instead of ``python -m http.server``
---------------------------------------------------------
``http.server`` would serve the three static files perfectly well, and the
backend does send CORS headers for ``http://127.0.0.1:5173``, so that route
works. This exists for the case where it does not: a different port, a
different host, or a browser that refuses the preflight. Proxying ``/api``
through the same origin removes cross-origin from the picture entirely, which
is one fewer thing to debug when the question is "does search work".

Standard library only — ``http.server`` plus ``urllib`` — so it adds no
dependency to ``requirements.txt`` for a throwaway tool.

It is a development tool and says so: it binds to localhost by default, it
forwards GET only, and it will not forward to a host that is not loopback.
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


class PreviewHandler(SimpleHTTPRequestHandler):
    """Static files from this directory; ``/api/*`` proxied to the backend."""

    api_base = "http://127.0.0.1:8000"

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        if self.path.startswith("/api/"):
            self._proxy()
            return
        super().do_GET()

    def _proxy(self) -> None:
        target = f"{self.api_base}{self.path}"
        try:
            with urllib.request.urlopen(target, timeout=30) as upstream:
                body = upstream.read()
                status = upstream.status
                content_type = upstream.headers.get(
                    "Content-Type", "application/json"
                )
        except urllib.error.HTTPError as exc:
            # The API's error envelope is part of its contract: pass the body
            # through unchanged so the page can show the real code and message.
            body = exc.read()
            status = exc.code
            content_type = exc.headers.get("Content-Type", "application/json")
        except urllib.error.URLError as exc:
            body = (
                b'{"error":{"code":"BACKEND_UNREACHABLE","message":'
                b'"the preview server could not reach the API: '
                + str(exc.reason).encode("utf-8", "replace")
                + b'","details":{}}}'
            )
            status = 502
            content_type = "application/json"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # A preview must never show a cached result while someone is changing
        # the query: a stale answer looks exactly like a wrong answer.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve the search preview.")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--api", default="http://127.0.0.1:8000",
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
        f"NiyamSetu search preview: http://{args.host}:{args.port}\n"
        f"proxying /api  ->  {PreviewHandler.api_base}\n"
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
