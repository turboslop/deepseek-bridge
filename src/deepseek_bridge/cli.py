from __future__ import annotations

import argparse
import contextlib
import signal
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deepseek_bridge import __version__

from .config import (
    ProxyConfig,
    _auto_pool_connections,
    _auto_queue_size,
    _auto_stream_timeout,
    default_config_path,
    default_reasoning_content_path,
)
from .handler import (
    DeepSeekProxyHandler,
)
from .helpers import (
    _handle_shutdown_signal,
    _shutdown_requested,
)
from .logging import LOG, configure_logging
from .reasoning_store import ReasoningStore, ReasoningStoreProtocol
from .server_infrastructure import (
    BoundedThreadPoolHTTPServer,
    UpstreamPool,
)
from .trace import TraceWriter
from .tunnel import (
    CloudflaredTunnel,
    HealthCheckConfig,
    NgrokTunnel,
    TunnelService,
    create_tunnel,
    get_tunnel_choices,
    local_tunnel_target,
)


def create_reasoning_store(config: ProxyConfig) -> ReasoningStoreProtocol:
    if config.storage_backend == "sqlite":
        return ReasoningStore(
            config.reasoning_content_path,
            max_age_seconds=config.reasoning_cache_max_age_seconds,
            max_rows=config.reasoning_cache_max_entries,
        )
    if config.storage_backend == "valkey":
        from .valkey_store import ValkeyReasoningStore

        return ValkeyReasoningStore(
            config.valkey_url,
            key_prefix=config.valkey_key_prefix,
            max_age_seconds=config.reasoning_cache_max_age_seconds,
            max_rows=config.reasoning_cache_max_entries,
            max_connections=config.max_thread_pool,
        )
    raise ValueError(f"storage backend {config.storage_backend!r} is invalid")


def _log_storage_startup(
    config: ProxyConfig, store: ReasoningStoreProtocol
) -> None:
    LOG.info("  Backend:      %s", config.storage_backend)
    try:
        stats = store.stats()
    except Exception as exc:
        LOG.warning("failed to read storage stats: %s", exc)
        return
    if stats.path:
        label = "Reasoning DB" if stats.backend == "sqlite" else "Store path"
        LOG.info("  %-12s %s", f"{label}:", stats.path)
    if stats.entries is not None:
        LOG.info("  Entries:      %s", stats.entries)


def _check_reasoning_cache_bloat(store: ReasoningStoreProtocol) -> None:
    check_bloat = getattr(store, "check_bloat", None)
    if not callable(check_bloat):
        return
    bloat_warning, _ = check_bloat()
    if bloat_warning:
        LOG.warning("reasoning cache health: %s", bloat_warning)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local DeepSeek Bridge proxy"
    )

    group_model = parser.add_argument_group("Model")
    group_model.add_argument(
        "--model",
        help=(
            "Fallback DeepSeek model when the request has no model, "
            "default from config or deepseek-v4-pro"
        ),
    )
    group_model.add_argument(
        "--thinking",
        choices=["enabled", "disabled"],
        help="DeepSeek thinking mode, default from config or enabled",
    )
    group_model.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high", "max", "xhigh"],
        help="DeepSeek reasoning effort, default from config or max",
    )
    group_model.add_argument(
        "--display-reasoning",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Mirror reasoning_content into Cursor-visible content",
    )
    group_model.add_argument(
        "--collapsible-reasoning",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Use Markdown details for mirrored reasoning when display is "
            "enabled"
        ),
    )

    group_net = parser.add_argument_group("Network")
    group_net.add_argument(
        "--host", help="Bind host, default from config or 127.0.0.1"
    )
    group_net.add_argument(
        "--port",
        type=int,
        help="Bind port, default from config or 9000",
    )
    group_net.add_argument(
        "--tunnel",
        choices=["none", *get_tunnel_choices()],
        default=None,
        help=(
            "Tunnel service for public URL exposure, default from config or "
            "cloudflared in local mode"
        ),
    )
    group_net.add_argument(
        "--cf-url",
        help="Cloudflare tunnel public URL (required for cloudflared tunnel)",
    )
    group_net.add_argument(
        "--ngrok-url",
        help="Fixed ngrok endpoint URL for reserved domains/endpoints",
    )
    group_net.add_argument(
        "--base-url",
        help=(
            "DeepSeek base URL, default from config or https://api.deepseek.com"
        ),
    )
    group_net.add_argument(
        "--cors",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Send CORS headers",
    )
    group_net.add_argument(
        "--cors-allowed-origin",
        action="append",
        dest="cors_allowed_origins",
        help=(
            "Allowed browser Origin for CORS; repeat for multiple origins. "
            "Use '*' only with --no-cors-allow-credentials for wildcard CORS."
        ),
    )
    group_net.add_argument(
        "--cors-allow-credentials",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow browser credentials for matching CORS origins",
    )

    group_storage = parser.add_argument_group("Storage")
    group_storage.add_argument(
        "--log-dir",
        type=Path,
        help=(
            "Write persistent timestamped log files to this directory "
            "(auto-purges old logs, keeps 5)"
        ),
    )
    group_storage.add_argument(
        "--no-log",
        action="store_true",
        help="Disable persistent log files (overrides default log directory)",
    )
    group_storage.add_argument(
        "--trace-dir",
        type=Path,
        help="Write full structured request traces to this directory",
    )
    group_storage.add_argument(
        "--reasoning-content-path",
        type=Path,
        help=(
            "SQLite reasoning_content cache path, "
            f"default {default_reasoning_content_path()}"
        ),
    )
    group_storage.add_argument(
        "--reasoning-cache-max-age-seconds",
        type=int,
        help="Maximum reasoning cache row age in seconds, default from config",
    )
    group_storage.add_argument(
        "--clear-reasoning-cache",
        action="store_true",
        help="Clear the configured reasoning_content cache and exit",
    )

    group_perf = parser.add_argument_group("Performance")
    group_perf.add_argument(
        "--request-timeout",
        type=float,
        help="Upstream request timeout in seconds, default from config or 300",
    )
    group_perf.add_argument(
        "--stream-read-timeout",
        type=float,
        help="Streaming read timeout in seconds, default from config or 180",
    )
    group_perf.add_argument(
        "--max-pool-connections",
        type=int,
        help="Maximum upstream pool connections, default from config or 10",
    )
    group_perf.add_argument(
        "--max-thread-pool",
        type=int,
        help=(
            "Maximum thread pool size for request handling, default from "
            "config or 20"
        ),
    )
    group_perf.add_argument(
        "--max-request-body-bytes",
        type=int,
        help="Maximum accepted request body size, default from config",
    )

    group_ollama = parser.add_argument_group("Ollama / Copilot")
    group_ollama.add_argument(
        "--ollama",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable Ollama-compatible endpoints",
    )

    group_other = parser.add_argument_group("Other")
    group_other.add_argument(
        "--runtime-mode",
        "--runtime",
        choices=["local", "kubernetes"],
        dest="runtime_mode",
        help=(
            "Runtime profile, default local. Kubernetes mode defaults to "
            "0.0.0.0, no tunnel, stdout logs, and in-memory cache."
        ),
    )
    group_other.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        help=f"YAML config file, default {default_config_path()}",
    )
    group_other.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG-level log output showing internal proxy decisions",
    )
    group_other.add_argument(
        "--compact",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Compact 1-line-per-request output",
    )
    group_other.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Run without interactive terminal UI affordances; useful for "
            "containers and services"
        ),
    )
    group_other.add_argument(
        "--missing-reasoning-strategy",
        choices=["recover", "reject"],
        help=(
            "What to do when required reasoning_content is missing: "
            "recover (friendly default) or reject (strict debugging mode)"
        ),
    )

    group_other.add_argument(
        "--version",
        action="version",
        version=f"deepseek-bridge {__version__}",
    )
    return parser


def warn_if_insecure_upstream(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http":
        return
    host = parsed.hostname or ""
    if host in {"127.0.0.1", "localhost", "::1"}:
        return
    LOG.warning(
        "upstream base_url uses plain HTTP; bearer tokens may be exposed"
    )


def _verify_tunnel_url(url: str, timeout: float = 10.0) -> bool:
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    for attempt in range(3):
        try:
            conn = (
                http.client.HTTPSConnection(host, port=port, timeout=timeout)
                if parsed.scheme == "https"
                else http.client.HTTPConnection(
                    host, port=port, timeout=timeout
                )
            )
            conn.request("GET", "/v1/health")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            conn.close()
            if resp.status == 200:
                LOG.info("tunnel health check: ok (%s)", url)
                return True
            if resp.status == 530:
                if attempt < 2:
                    time.sleep(5)
                    continue
                LOG.warning(
                    "tunnel health check: HTTP 530 at %s — Cloudflare tunnel "
                    "not connected. Run 'cloudflared tunnel list' and "
                    "'cloudflared tunnel route dns'. "
                    "Body: %s",
                    url,
                    body[:120],
                )
                return False
            LOG.warning(
                "tunnel health check: HTTP %s at %s — %s",
                resp.status,
                url,
                body[:120],
            )
            return False
        except Exception as exc:
            if attempt < 2:
                time.sleep(5)
                continue
            LOG.warning(
                "tunnel health check: failed to reach %s — %s", url, exc
            )
            return False
    return False


def _run_server(server: BoundedThreadPoolHTTPServer) -> None:
    server.timeout = 0.5
    while not _shutdown_requested.is_set():
        server.handle_request()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    _shutdown_requested.clear()
    try:
        config = ProxyConfig.from_file(
            config_path=args.config_path,
            runtime_mode=args.runtime_mode,
        )
    except ValueError as exc:
        configure_logging(debug=bool(args.debug))
        LOG.error("%s", exc)
        return 2
    updates: dict[str, Any] = {}
    if args.host is not None:
        updates["host"] = args.host
    if args.port is not None:
        updates["port"] = args.port
    if args.model is not None:
        updates["upstream_model"] = args.model
    if args.base_url is not None:
        updates["upstream_base_url"] = args.base_url.rstrip("/")
    if args.thinking is not None:
        updates["thinking"] = args.thinking
    if args.reasoning_effort is not None:
        updates["reasoning_effort"] = args.reasoning_effort
    if args.reasoning_content_path is not None:
        updates["reasoning_content_path"] = args.reasoning_content_path
    if args.tunnel is not None:
        updates["tunnel"] = args.tunnel
    if args.cf_url is not None:
        updates["cf_url"] = args.cf_url
    if args.ngrok_url is not None:
        updates["ngrok_url"] = args.ngrok_url
    if args.debug:
        updates["debug"] = True
    if args.compact is not None:
        updates["compact"] = args.compact
    if args.headless:
        updates["compact"] = True
    if args.trace_dir is not None:
        updates["trace_dir"] = args.trace_dir
    if args.display_reasoning is not None:
        updates["display_reasoning"] = args.display_reasoning
    if args.collapsible_reasoning is not None:
        updates["collapsible_reasoning"] = args.collapsible_reasoning
    if args.cors is not None:
        updates["cors"] = args.cors
    if args.cors_allowed_origins is not None:
        updates["cors_allowed_origins"] = tuple(args.cors_allowed_origins)
    if args.cors_allow_credentials is not None:
        updates["cors_allow_credentials"] = args.cors_allow_credentials
    if args.ollama is not None:
        updates["ollama"] = args.ollama
    if args.request_timeout is not None:
        updates["request_timeout"] = args.request_timeout
        if (
            args.stream_read_timeout is None
            and config.stream_read_timeout
            == _auto_stream_timeout(config.request_timeout)
        ):
            updates["stream_read_timeout"] = _auto_stream_timeout(
                args.request_timeout
            )
    if args.stream_read_timeout is not None:
        updates["stream_read_timeout"] = args.stream_read_timeout
    if args.max_request_body_bytes is not None:
        updates["max_request_body_bytes"] = args.max_request_body_bytes
    if args.reasoning_cache_max_age_seconds is not None:
        updates["reasoning_cache_max_age_seconds"] = (
            args.reasoning_cache_max_age_seconds
        )
    if args.missing_reasoning_strategy is not None:
        updates["missing_reasoning_strategy"] = args.missing_reasoning_strategy
    if args.max_pool_connections is not None:
        updates["max_pool_connections"] = args.max_pool_connections
    if args.max_thread_pool is not None:
        updates["max_thread_pool"] = args.max_thread_pool
        updates["max_queue_size"] = _auto_queue_size(args.max_thread_pool)
        if (
            args.max_pool_connections is None
            and config.max_pool_connections
            == _auto_pool_connections(config.max_thread_pool)
        ):
            updates["max_pool_connections"] = _auto_pool_connections(
                args.max_thread_pool
            )
    if updates:
        config = replace(config, **updates)

    if args.no_log:
        config = replace(config, log_dir=None)

    log_dir = None if args.no_log else (args.log_dir or config.log_dir)
    log_file_path = configure_logging(debug=config.debug, log_dir=log_dir)
    if config.runtime_mode == "kubernetes" and config.tunnel != "none":
        LOG.error(
            "kubernetes runtime requires tunnel=none; got %s", config.tunnel
        )
        return 2
    warn_if_insecure_upstream(config.upstream_base_url)
    try:
        store = create_reasoning_store(config)
    except ValueError as exc:
        LOG.error("%s", exc)
        return 2
    _check_reasoning_cache_bloat(store)
    store.start_periodic_maintenance()
    if args.clear_reasoning_cache:
        deleted = store.clear()
        LOG.info("cleared %s reasoning cache row(s)", deleted)
        store.close()
        return 0
    trace_writer: TraceWriter | None = None
    if config.trace_dir is not None:
        try:
            trace_writer = TraceWriter(config.trace_dir)
        except OSError as exc:
            LOG.error("failed to initialize trace directory: %s", exc)
            store.close()
            return 2
    pool = UpstreamPool(max_connections=config.max_pool_connections)
    server = BoundedThreadPoolHTTPServer(
        (config.host, config.port),
        DeepSeekProxyHandler,
        max_workers=config.max_thread_pool,
    )
    server.config = config
    server.reasoning_store = store
    server.trace_writer = trace_writer
    server.upstream_pool = pool
    server.start_time = time.monotonic()

    # GC tuning: reduce collection frequency during streaming to save CPU.
    # gc.freeze() excludes all currently-allocated objects from GC scans.
    # gc.set_threshold(50000, 10, 10) dramatically reduces Gen 2 collections.
    import gc

    gc.freeze()
    gc.set_threshold(50000, 10, 10)

    tunnel: TunnelService | None = None
    public_url: str | None = None
    if config.tunnel != "none":
        target_url = local_tunnel_target(config.host, config.port)
        tunnel = create_tunnel(config.tunnel, target_url)
        if isinstance(tunnel, CloudflaredTunnel):
            tunnel.cfd_url = config.cf_url
            tunnel.cfd_tunnel_name = getattr(
                config, "cfd_tunnel_name", "deepseek-bridge"
            )
        if isinstance(tunnel, NgrokTunnel) and config.ngrok_url:
            tunnel.ngrok_url = config.ngrok_url
        try:
            public_url = tunnel.start()
            # Run health check in background — don't block server startup
            threading.Thread(
                target=_verify_tunnel_url,
                args=(public_url,),
                daemon=True,
            ).start()
        except RuntimeError as exc:
            LOG.error("%s", exc)
            server.server_close()
            store.close()
            return 2
        if isinstance(tunnel, NgrokTunnel):
            tunnel.health_check = HealthCheckConfig(
                check_interval=30,
            )
            tunnel.start_health_check()
    server.public_url = public_url
    local_base_url = f"http://{config.host}:{config.port}/v1"
    api_base_url = (
        f"{public_url.rstrip('/')}/v1"
        if public_url is not None
        else local_base_url
    )

    # ── Startup Banner ──────────────────────────────────────────
    LOG.info("")
    LOG.info("╔══════════════════════════════════════════════╗")
    LOG.info("║   DeepSeek Bridge v%s                     ║", __version__)
    LOG.info("╚══════════════════════════════════════════════╝")
    LOG.info("")
    LOG.info("Model")
    LOG.info(
        "  %s (%s, %s)",
        config.upstream_model,
        "thinking" if config.thinking == "enabled" else "no thinking",
        config.reasoning_effort,
    )
    display_reasoning = "off"
    if config.display_reasoning:
        display_reasoning = (
            "on (collapsible)" if config.collapsible_reasoning else "on"
        )
    LOG.info("  Display reasoning: %s", display_reasoning)
    if config.debug:
        LOG.info(
            "  Missing reasoning strategy: %s",
            config.missing_reasoning_strategy,
        )
    LOG.info("")
    LOG.info("Network")
    LOG.info("  Local:     %s", local_base_url)
    LOG.info("  API Base:  %s", api_base_url)
    if config.runtime_mode != "local":
        LOG.info("  Runtime:   %s", config.runtime_mode)
    if public_url is not None:
        LOG.info("  Tunnel:    %s", public_url)
    elif config.tunnel == "none":
        LOG.info("  Tunnel:    none")
    LOG.info("  Ollama:    %s", "enabled" if config.ollama else "disabled")
    LOG.info("")
    LOG.info("Storage")
    _log_storage_startup(config, store)
    if log_file_path:
        LOG.info("  Logs:         %s", log_file_path)
    else:
        LOG.info("  Logs:         disabled")
    LOG.info("")
    if config.debug:
        LOG.warning("debug mode: request/response logging enabled")
    if trace_writer is not None:
        LOG.info("Trace dir: %s", trace_writer.session_dir)
        LOG.warning(
            "trace logging enabled; prompts and code will be written to disk"
        )
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, _handle_shutdown_signal)
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    try:
        try:
            _run_server(server)
        except KeyboardInterrupt:
            LOG.info("received SIGINT, initiating graceful shutdown")
            _shutdown_requested.set()
    finally:
        if isinstance(server, BoundedThreadPoolHTTPServer):
            LOG.info("graceful shutdown: stopping new connections...")
            server.server_close()
        _shutdown_requested.set()
        if tunnel is not None:
            tunnel.stop()
        store.prune()
        store.close()
        if hasattr(gc, "unfreeze"):
            gc.unfreeze()
        LOG.info("graceful shutdown: complete")
        _shutdown_requested.clear()
    return 0


if __name__ == "__main__":
    sys.exit(main())
