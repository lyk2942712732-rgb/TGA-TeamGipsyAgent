"""Serve the built SPA with index.html fallback for Playwright."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SPAHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        requested = self.translate_path(self.path.split("?", 1)[0].split("#", 1)[0])
        if not Path(requested).exists():
            self.path = "/index.html"
        return super().send_head()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    handler = lambda *values, **kwargs: SPAHandler(*values, directory=args.directory, **kwargs)
    ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
