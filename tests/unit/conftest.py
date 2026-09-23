"""The published site, stood in for by a server on loopback.

Two scripts read the previous publication back from the address the showcase is
served from: `scripts/build-showcase.sh` fetches the Allure history the report's
trend is drawn from, and `scripts/restore-db.sh` fetches the database tonight's
diff is made against. Both take that address from `SITE_URL`, so their tests point
it here, at a directory laid out the way the real site is, and nothing they run
reaches the internet.
"""
from __future__ import annotations

import functools
import http.server
import os
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

#: Proxy settings a developer's shell may carry. A request for loopback routed
#: through one of them would test the proxy rather than the script, so the
#: script tests run without them.
PROXY_VARIABLES = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy")


@pytest.fixture
def script_env() -> dict[str, str]:
    """The environment a script under test starts from: this one, without a proxy."""
    return {name: value for name, value in os.environ.items() if name not in PROXY_VARIABLES}


@dataclass
class PublishedSite:
    """What a test can do with the stand-in: lay files out, point a script at it, see what was asked."""

    root: Path
    url: str
    requested: list[str]

    def publish(self, path: str, body: str | bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, str):
            target.write_text(body, encoding="utf-8")
        else:
            target.write_bytes(body)


@pytest.fixture
def published_site(tmp_path: Path) -> Iterator[PublishedSite]:
    """A static server over an empty directory; a file answers 200, anything else 404."""
    root = tmp_path / "published-site"
    root.mkdir()
    requested: list[str] = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            requested.append(self.path)
            super().do_GET()

        def log_message(self, *args: object) -> None:
            """Quiet: the test states what it expected to be asked instead."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield PublishedSite(root=root, url=f"http://127.0.0.1:{server.server_address[1]}/", requested=requested)
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def unreachable_site_url() -> str:
    """An address on loopback with nothing listening: the connection is refused at once."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/"
