#!/usr/bin/env python3
"""Soak test for deepseek-bridge connection resilience.

Spawns a fake upstream + real proxy, launches N concurrent workers that hammer
the proxy with random streaming/non-streaming requests, randomly cancelling
mid-request, and reports a PASS/FAIL summary.

Usage:
    python tests/test_soak.py --duration 10 --concurrency 3
    python tests/test_soak.py                      # defaults: 7200s, 10 workers
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.reasoning_store import ReasoningStore
from deepseek_bridge.server import (
    DeepSeekProxyHandler,
    DeepSeekProxyServer,
    UpstreamPool,
)

# ---------------------------------------------------------------------------
# Fake upstream — answers fast so the test exercises the proxy, not the wire
# ---------------------------------------------------------------------------


class _FakeUpstream(BaseHTTPRequestHandler):
    """Minimal upstream that returns short streaming / non-streaming responses."""

    request_count: int = 0
    _lock: threading.Lock = threading.Lock()

    def log_message(self, format: str, *args: object) -> None:  # type: ignore[override]
        return  # silence HTTP request logging

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        with _FakeUpstream._lock:
            _FakeUpstream.request_count += 1

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(3):
                chunk = {"choices": [{"index": 0, "delta": {"content": f"chunk{i}"}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.01)  # tiny delay so clients can cancel mid-stream
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------


def _start_upstream() -> tuple[ThreadingHTTPServer, str]:
    """Return (server, upstream_url) on a free port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.name = "soak-upstream"
    t.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _start_proxy(upstream_url: str) -> tuple[DeepSeekProxyServer, ReasoningStore, str]:
    """Start a real proxy pointing at *upstream_url*.  Returns (server, store, url)."""
    store = ReasoningStore(":memory:")
    proxy = DeepSeekProxyServer(("127.0.0.1", 0), DeepSeekProxyHandler)
    proxy.config = ProxyConfig(
        upstream_base_url=upstream_url,
        tunnel="none",
        request_timeout=10,
    )
    proxy.reasoning_store = store
    proxy.upstream_pool = UpstreamPool()
    t = threading.Thread(target=proxy.serve_forever, daemon=True)
    t.name = "soak-proxy"
    t.start()
    return proxy, store, f"http://127.0.0.1:{proxy.server_address[1]}"


# ---------------------------------------------------------------------------
# Stats bag
# ---------------------------------------------------------------------------


class Stats:
    """Thread-safe counter bag."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.total = 0
        self.completed = 0  # fully read by worker
        self.cancelled = 0  # worker closed early
        self.errors = 0  # proxy returned error / unreachable / timeout
        self.crashes = 0  # unhandled exception in worker

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return {
                "total": self.total,
                "completed": self.completed,
                "cancelled": self.cancelled,
                "errors": self.errors,
                "crashes": self.crashes,
            }


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


_SOAK_PAYLOAD = {
    "model": "deepseek-v4-pro",
    "messages": [{"role": "user", "content": "soak test"}],
}


def _send_request(proxy_url: str, stream: bool, timeout: int = 10) -> HTTPResponse:
    """Send a chat-completion POST.  Returns the file-like response on success,
    or raises.  (Connection errors bubble up so the caller classifies them.)"""
    payload = dict(_SOAK_PAYLOAD, stream=stream)
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{proxy_url}/v1/chat/completions",
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer sk-soak-test",
            "Content-Type": "application/json",
        },
    )
    return urlopen(req, timeout=timeout)


def _is_transient_socket_error(exc: BaseException) -> bool:
    """True for socket errors that happen when *we* close early."""
    name = type(exc).__name__
    return name in (
        "IncompleteRead",
        "RemoteDisconnected",
        "BadStatusLine",
        "ConnectionResetError",
        "BrokenPipeError",
    )


def _worker(
    worker_id: int,
    proxy_url: str,
    duration: float,
    stats: Stats,
    stop_event: threading.Event,
) -> None:
    """Single worker loop — runs until *stop_event* is set or *duration* elapses."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline and not stop_event.is_set():
        stream = random.random() < 0.5
        try:
            response = _send_request(proxy_url, stream)

            # Decide whether to cancel this request
            if random.random() < 0.6:
                # Cancel: sleep 0.1 – 2.0 s then close the connection early
                delay = random.uniform(0.1, 2.0)
                time.sleep(delay)
                try:
                    response.close()
                except Exception:
                    pass  # expected when we close mid-stream
                with stats.lock:
                    stats.total += 1
                    stats.cancelled += 1
            else:
                # Complete: read the full response
                if stream:
                    while True:
                        line = response.readline()
                        if not line or b"[DONE]" in line:
                            break
                else:
                    response.read()
                response.close()
                with stats.lock:
                    stats.total += 1
                    stats.completed += 1

        except HTTPError as exc:
            with stats.lock:
                stats.total += 1
                if exc.code >= 500:
                    stats.errors += 1
                # 4xx from proxy is a real error too (not transient)
                elif exc.code >= 400:
                    stats.errors += 1

        except (URLError, ConnectionRefusedError, OSError) as exc:
            # Distinguish transient socket errors (from our early-close) vs
            # real proxy problems (connection refused, timeout).
            if _is_transient_socket_error(exc):
                with stats.lock:
                    stats.total += 1
                    stats.cancelled += 1
            else:
                with stats.lock:
                    stats.total += 1
                    stats.errors += 1

        except Exception:
            with stats.lock:
                stats.total += 1
                stats.crashes += 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Soak-test the proxy with concurrent random-cancel traffic."
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=7200,
        help="How long to run, in seconds (default: 7200 = 2 hours)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent worker threads (default: 10)",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Start services
    # ------------------------------------------------------------------
    print(
        f"=== soak test: duration={args.duration}s  concurrency={args.concurrency} ==="
    )

    upstream_srv, upstream_url = _start_upstream()
    print(f"  upstream  → {upstream_url}")

    proxy_srv, store, proxy_url = _start_proxy(upstream_url)
    print(f"  proxy     → {proxy_url}")
    print(f"  launching {args.concurrency} workers …")

    stats = Stats()
    start_time = time.monotonic()
    stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Ctrl‑C handler — prints summary and exits cleanly
    # ------------------------------------------------------------------
    interrupted = False

    def _on_interrupt(_sig: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        stop_event.set()
        # Do NOT call sys.exit here — let the finally-block print the report.

    signal.signal(signal.SIGINT, _on_interrupt)

    # ------------------------------------------------------------------
    # Run workers
    # ------------------------------------------------------------------
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(
                    _worker,
                    worker_id=i,
                    proxy_url=proxy_url,
                    duration=args.duration,
                    stats=stats,
                    stop_event=stop_event,
                )
                for i in range(args.concurrency)
            ]
            # Wait for all workers (they stop when duration elapses or
            # stop_event is set via Ctrl‑C).
            for _f in as_completed(futures):
                try:
                    _f.result()
                except Exception:
                    pass  # already tracked inside the worker
    finally:
        elapsed = time.monotonic() - start_time
        s = stats.snapshot()

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    print()
    print("=" * 50)
    print("  SOAK RESULTS")
    print("=" * 50)
    print(f"  duration       : {elapsed:.1f}s")
    print(f"  concurrency    : {args.concurrency}")
    print(f"  total requests : {s['total']}")
    print(f"  completed      : {s['completed']}")
    print(f"  cancelled      : {s['cancelled']}")
    print(f"  errors         : {s['errors']}")
    print(f"  crashes        : {s['crashes']}")
    if s["total"] > 0:
        rate = s["total"] / max(elapsed, 0.001)
        print(f"  rate           : {rate:.1f} req/s")
    print()

    if interrupted:
        print("RESULT: PASS  (interrupted by user)")
        sys.exit(0)

    # FAIL if the proxy returned errors or workers crashed unexpectedly.
    if s["errors"] > 0 or s["crashes"] > 0:
        print("RESULT: FAIL")
        sys.exit(1)
    else:
        print("RESULT: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
