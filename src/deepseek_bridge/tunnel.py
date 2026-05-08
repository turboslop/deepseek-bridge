from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlparse

from .logging import LOG

_tunnel_registry: dict[str, type["TunnelService"]] = {}
"""Registry of tunnel implementations, populated via __init_subclass__."""

DEFAULT_NGROK_API_URL = "http://127.0.0.1:4040/api"


def local_tunnel_target(host: str, port: int) -> str:
    local_host = host.strip() or "127.0.0.1"
    if local_host in {"0.0.0.0", "::"}:
        local_host = "127.0.0.1"
    if ":" in local_host and not local_host.startswith("["):
        local_host = f"[{local_host}]"
    return f"http://{local_host}:{port}"


def parse_ngrok_public_url(payload: dict[str, Any]) -> str | None:
    records = payload.get("endpoints")
    if not isinstance(records, list):
        records = payload.get("tunnels")
    if not isinstance(records, list):
        return None

    public_urls = [
        public_url
        for record in records
        if isinstance(record, dict)
        for public_url in (record.get("url"), record.get("public_url"))
        if isinstance(public_url, str)
    ]
    for public_url in public_urls:
        if public_url.startswith("https://"):
            return public_url
    for public_url in public_urls:
        if public_url.startswith("http://"):
            return public_url
    return None


def ngrok_agent_urls(api_url: str) -> list[str]:
    normalized = api_url.rstrip("/")
    if normalized.endswith("/endpoints") or normalized.endswith("/tunnels"):
        return [normalized]
    return [f"{normalized}/endpoints", f"{normalized}/tunnels"]


@dataclass
class HealthCheckConfig:
    check_interval: float = 30.0
    recovery_max_retries: int = 3
    recovery_retry_delay: float = 5.0


class TunnelService(ABC):
    """Abstract base class for tunnel services (ngrok, localhost.run, etc.)."""

    public_url: str | None = None
    tunnel_name: str = ""  # Set by subclasses (e.g. "ngrok", "localhostrun")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.tunnel_name:
            _tunnel_registry[cls.tunnel_name] = cls

    @abstractmethod
    def start(self) -> str:
        """Start the tunnel. Returns the public URL."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the tunnel."""
        ...


@dataclass
class NgrokTunnel(TunnelService):
    tunnel_name = "ngrok"

    target_url: str
    command: str = "ngrok"
    api_url: str = DEFAULT_NGROK_API_URL
    startup_timeout: float = 15.0
    health_check: HealthCheckConfig | None = None

    process: subprocess.Popen[bytes] | None = None
    public_url: str | None = field(default=None, init=False)

    _running: bool = field(default=True, init=False)
    _health_thread: threading.Thread | None = field(default=None, init=False)

    def start(self) -> str:
        if shutil.which(self.command) is None:
            raise RuntimeError(
                "ngrok is not installed or is not on PATH. Install it, then run "
                "`ngrok config add-authtoken <token>` once."
            )

        self._running = True
        self.process = subprocess.Popen(
            [self.command, "http", self.target_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            public_url = self.wait_for_public_url()
            self.public_url = public_url
            return public_url
        except Exception as exc:
            LOG.warning("ngrok tunnel start failed: %s", exc)
            self.stop()
            raise

    def wait_for_public_url(self) -> str:
        deadline = time.monotonic() + self.startup_timeout
        last_error = "ngrok did not report a public URL"
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError("ngrok exited before creating a tunnel")
            for api_url in ngrok_agent_urls(self.api_url):
                try:
                    with urlopen(api_url, timeout=1) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    public_url = parse_ngrok_public_url(payload)
                    if public_url:
                        return public_url
                except (OSError, URLError, json.JSONDecodeError) as exc:
                    last_error = str(exc)
            time.sleep(0.25)
        raise RuntimeError(f"Timed out waiting for ngrok tunnel: {last_error}")

    def stop(self) -> None:
        self._running = False
        if self.process is None or self.process.poll() is not None:
            return
        LOG.info("stopping ngrok tunnel")
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def start_health_check(self) -> None:
        if self.health_check is None:
            return
        self._health_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
        )
        self._health_thread.start()
        LOG.info(
            "ngrok health check: started (interval=%ss)",
            self.health_check.check_interval,
        )

    def _is_healthy(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        for api_url in ngrok_agent_urls(self.api_url):
            try:
                with urlopen(api_url, timeout=1) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                public_url = parse_ngrok_public_url(payload)
                if public_url:
                    return True
            except (OSError, URLError, json.JSONDecodeError):
                continue
        return False

    def _health_check_loop(self) -> None:
        hc = self.health_check
        if hc is None:
            return
        while self._running:
            time.sleep(hc.check_interval)
            if self._is_healthy():
                continue
            LOG.warning("ngrok tunnel health check failed, attempting recovery")
            recovered = False
            for attempt in range(1, hc.recovery_max_retries + 1):
                try:
                    self.stop()
                    time.sleep(hc.recovery_retry_delay)
                    public_url = self.start()
                    LOG.info(
                        "ngrok tunnel recovered, new URL: %s",
                        public_url,
                    )
                    self.public_url = public_url
                    recovered = True
                    break
                except Exception as exc:
                    LOG.warning(
                        "ngrok recovery attempt %s/%s failed: %s",
                        attempt,
                        hc.recovery_max_retries,
                        exc,
                    )
            if not recovered:
                LOG.critical(
                    "ngrok tunnel recovery failed after %s retries",
                    hc.recovery_max_retries,
                )


LOCALHOSTRUN_URL_PATTERN = re.compile(
    r"https?://[a-zA-Z0-9-]{8,}\.(?:lhr\.life|localhost\.run)\b"
)

LOCALHOSTRUN_TUNNEL_LINE = re.compile(r"tunneled with tls termination")


@dataclass
class LocalhostRunTunnel(TunnelService):
    """Tunnel via SSH reverse port forwarding to localhost.run."""

    tunnel_name = "localhostrun"

    target_url: str
    timeout: float = 15.0

    process: subprocess.Popen[str] | None = field(default=None, init=False)
    public_url: str | None = field(default=None, init=False)

    def start(self) -> str:
        host, port = self._parse_target()
        cmd = ["ssh", "-R", f"80:{host}:{port}", "nokey@localhost.run"]
        LOG.info("starting localhost.run tunnel: %s", " ".join(cmd))
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            public_url = self._wait_for_url()
            self.public_url = public_url
            LOG.info("localhost.run tunnel established: %s", public_url)
            return public_url
        except Exception as exc:
            LOG.warning("localhost.run tunnel start failed: %s", exc)
            self.stop()
            raise

    def _parse_target(self) -> tuple[str, int]:
        parsed = urlparse(self.target_url)
        host = parsed.hostname or "127.0.0.1"
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        port = parsed.port or 80
        return host, port

    def _wait_for_url(self) -> str:
        assert self.process is not None
        assert self.process.stdout is not None
        deadline = time.monotonic() + self.timeout
        for line in self.process.stdout:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timed out waiting for localhost.run tunnel URL"
                )
            # Only match lines that are actually tunnel notifications,
            # not documentation URLs in the SSH welcome banner.
            if LOCALHOSTRUN_TUNNEL_LINE.search(line):
                match = LOCALHOSTRUN_URL_PATTERN.search(line)
                if match:
                    url = match.group(0)
                    if url.startswith("http://"):
                        url = "https://" + url[len("http://"):]
                    return url
        raise RuntimeError(
            "SSH process exited before reporting a tunnel URL"
        )

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        LOG.info("stopping localhost.run tunnel")
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def get_tunnel_choices() -> list[str]:
    """Return the list of registered tunnel kind names."""
    return list(_tunnel_registry.keys())


def create_tunnel(kind: str, target_url: str) -> TunnelService:
    """Factory: return a tunnel of the requested kind."""
    cls = _tunnel_registry.get(kind)
    if cls is None:
        raise ValueError(f"unknown tunnel kind: {kind}")
    return cls(target_url)
