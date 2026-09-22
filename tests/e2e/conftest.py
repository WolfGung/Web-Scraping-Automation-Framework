"""Runs the demo store as a real HTTP server for the length of the e2e session.

A source is only proven against a live server, not a mock of one — so this starts
`uvicorn` in a background thread on a free port, the same as anyone running
`scrapewatch.demo_store.app` for real would, and tears it down when the session ends.
"""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

from scrapewatch.demo_store.app import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def demo_store_url() -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/api/products?page=1", timeout=1.0).status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError("demo store did not come up within 10s")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5.0)
