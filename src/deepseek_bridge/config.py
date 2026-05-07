from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import os

import yaml

APP_DIR_NAME = ".deepseek-bridge"
CONFIG_FILE_NAME = "config.yaml"
REASONING_CONTENT_FILE_NAME = "reasoning_content.sqlite3"

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
MISSING = object()

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000
DEFAULT_UPSTREAM_BASE_URL = "https://api.deepseek.com"
DEFAULT_UPSTREAM_MODEL = "deepseek-v4-pro"
DEFAULT_THINKING = "enabled"
DEFAULT_REASONING_EFFORT = "max"
DEFAULT_DISPLAY_REASONING = True
DEFAULT_COLLAPSIBLE_REASONING = True
DEFAULT_NGROK = True
DEFAULT_VERBOSE = False
DEFAULT_DEBUG = False
DEFAULT_REQUEST_TIMEOUT = 300.0
DEFAULT_STREAM_READ_TIMEOUT = 180.0
DEFAULT_MAX_REQUEST_BODY_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_POOL_CONNECTIONS = 10
DEFAULT_MAX_THREAD_POOL = max(os.cpu_count() or 4, 8)
DEFAULT_CORS = True
DEFAULT_MISSING_REASONING_STRATEGY = "recover"
DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_REASONING_CACHE_DISK_MB = 500
DEFAULT_NGROK_HEALTH_CHECK_INTERVAL = 30.0
DEFAULT_LOG_DIR = str(Path.home() / APP_DIR_NAME / "logs")

DEFAULT_CONFIG_HEADER = (
    "# This file was created automatically at ~/.deepseek-bridge/config.yaml."
)
DEFAULT_CONFIG_TEXT = f"""{DEFAULT_CONFIG_HEADER}
# API keys are read from Cursor's Authorization header and forwarded upstream.

# Essential settings — these are the ones you'll most likely customize
model: {DEFAULT_UPSTREAM_MODEL}
base_url: {DEFAULT_UPSTREAM_BASE_URL}
thinking: {DEFAULT_THINKING}
reasoning_effort: {DEFAULT_REASONING_EFFORT}
display_reasoning: {str(DEFAULT_DISPLAY_REASONING).lower()}
collapsible_reasoning: {str(DEFAULT_COLLAPSIBLE_REASONING).lower()}

host: {DEFAULT_HOST}
port: {DEFAULT_PORT}
ngrok: {str(DEFAULT_NGROK).lower()}
verbose: {str(DEFAULT_VERBOSE).lower()}
cors: {str(DEFAULT_CORS).lower()}
request_timeout: {DEFAULT_REQUEST_TIMEOUT:g}
max_request_body_bytes: {DEFAULT_MAX_REQUEST_BODY_BYTES}

# Advanced — defaults are fine for most users
# missing_reasoning_strategy: {DEFAULT_MISSING_REASONING_STRATEGY}
# reasoning_content_path: {REASONING_CONTENT_FILE_NAME}  # auto: ~/.deepseek-bridge/reasoning_content.sqlite3
# reasoning_cache_max_age_seconds: {DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS}
# log_dir: null  # auto: ~/.deepseek-bridge/logs
"""


def default_app_dir() -> Path:
    return Path.home() / APP_DIR_NAME


def default_config_path() -> Path:
    return default_app_dir() / CONFIG_FILE_NAME


def default_reasoning_content_path() -> Path:
    return default_app_dir() / REASONING_CONTENT_FILE_NAME


def default_log_dir() -> Path:
    return default_app_dir() / "logs"


def populate_default_config_file(config_path: Path) -> None:
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path.parent.chmod(0o700)
    config_path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    config_path.chmod(0o600)


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        return {}

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML config at {config_path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"Config file must contain a YAML mapping: {config_path}")
    return dict(loaded)


def resolve_config_path(config_path: str | Path | None) -> Path:
    return Path(config_path or default_config_path()).expanduser()


def setting_value(settings: Mapping[str, Any], key: str) -> Any:
    return settings.get(key, MISSING)


def setting_value_any(settings: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = setting_value(settings, key)
        if value is not MISSING:
            return value
    return MISSING


def as_str(value: Any, default: str) -> str:
    if value is MISSING or value is None:
        return default
    return str(value)


def as_bool(value: Any, default: bool) -> bool:
    if value is MISSING or value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def as_int(value: Any, default: int) -> int:
    if value is MISSING or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float) -> float:
    if value is MISSING or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_path(value: Any, default_path: Path, relative_base: Path) -> Path:
    if value is MISSING or value is None or value == "":
        return default_path
    candidate_path = Path(str(value)).expanduser()
    if candidate_path.is_absolute():
        return candidate_path
    return relative_base / candidate_path


def settings_from_config(
    config_path: str | Path | None,
) -> tuple[dict[str, Any], Path]:
    resolved_config_path = resolve_config_path(config_path)
    if config_path is None and not resolved_config_path.exists():
        populate_default_config_file(resolved_config_path)
    return load_config_file(resolved_config_path), resolved_config_path


def normalize_thinking(value: Any) -> str:
    thinking = as_str(value, DEFAULT_THINKING).strip().lower()
    if thinking in {"enabled", "disabled"}:
        return thinking
    return DEFAULT_THINKING


def normalize_missing_reasoning_strategy(value: Any) -> str:
    strategy = as_str(value, DEFAULT_MISSING_REASONING_STRATEGY).strip().lower()
    if strategy in {"recover", "reject"}:
        return strategy
    return DEFAULT_MISSING_REASONING_STRATEGY


def _auto_stream_timeout(request_timeout: float, explicit: Any = None) -> float:
    if explicit is not None:
        return as_float(explicit, DEFAULT_STREAM_READ_TIMEOUT)
    return max(request_timeout * 0.6, 60.0)


def _auto_pool_connections(max_thread_pool: int, explicit: Any = None) -> int:
    if explicit is not None:
        return as_int(explicit, DEFAULT_MAX_POOL_CONNECTIONS)
    return max(max_thread_pool // 2, 5)


def _auto_queue_size(max_thread_pool: int) -> int:
    """Auto-calculate queue size from thread pool count."""
    return max_thread_pool * 2 + 10


def _auto_cache_max_rows(disk_budget_mb: int = DEFAULT_REASONING_CACHE_DISK_MB) -> int:
    """Auto-calculate max rows based on disk budget."""
    try:
        import shutil

        available_gb = shutil.disk_usage(default_app_dir()).free / (1024**3)
        budget_mb = min(disk_budget_mb, available_gb * 1024 * 0.05)
    except Exception:
        budget_mb = disk_budget_mb
    est_row_bytes = 1500  # avg ~1.5KB per row
    return max(int((budget_mb * 1024 * 1024) / est_row_bytes), 10000)


@dataclass(frozen=True)
class ProxyConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    upstream_base_url: str = DEFAULT_UPSTREAM_BASE_URL
    upstream_model: str = DEFAULT_UPSTREAM_MODEL
    thinking: str = DEFAULT_THINKING
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    stream_read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES
    reasoning_content_path: Path = field(default_factory=default_reasoning_content_path)
    missing_reasoning_strategy: str = DEFAULT_MISSING_REASONING_STRATEGY
    reasoning_cache_max_age_seconds: int = DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS
    display_reasoning: bool = DEFAULT_DISPLAY_REASONING
    collapsible_reasoning: bool = DEFAULT_COLLAPSIBLE_REASONING
    max_pool_connections: int = DEFAULT_MAX_POOL_CONNECTIONS
    max_thread_pool: int = DEFAULT_MAX_THREAD_POOL
    max_queue_size: int = field(default_factory=lambda: _auto_queue_size(DEFAULT_MAX_THREAD_POOL))
    cors: bool = DEFAULT_CORS
    ollama: bool = True
    verbose: bool = DEFAULT_VERBOSE
    debug: bool = False
    compact: bool = False
    ngrok: bool = DEFAULT_NGROK
    ngrok_health_check_interval: float = DEFAULT_NGROK_HEALTH_CHECK_INTERVAL
    trace_dir: Path | None = None
    log_dir: Path | None = field(default_factory=default_log_dir)

    @classmethod
    def from_file(
        cls: type[ProxyConfig],
        config_path: str | Path | None = None,
    ) -> ProxyConfig:
        settings, resolved_config_path = settings_from_config(config_path)
        config_dir = resolved_config_path.parent

        return cls(
            host=as_str(
                setting_value(settings, "host"),
                DEFAULT_HOST,
            ),
            port=as_int(
                setting_value(settings, "port"),
                DEFAULT_PORT,
            ),
            upstream_base_url=as_str(
                setting_value(settings, "base_url"),
                DEFAULT_UPSTREAM_BASE_URL,
            ).rstrip("/"),
            upstream_model=as_str(
                setting_value(settings, "model"),
                DEFAULT_UPSTREAM_MODEL,
            ),
            thinking=normalize_thinking(setting_value(settings, "thinking")),
            reasoning_effort=as_str(
                setting_value(settings, "reasoning_effort"),
                DEFAULT_REASONING_EFFORT,
            ),
            request_timeout=as_float(
                setting_value(settings, "request_timeout"),
                DEFAULT_REQUEST_TIMEOUT,
            ),
            stream_read_timeout=_auto_stream_timeout(
                as_float(
                    setting_value(settings, "request_timeout"), DEFAULT_REQUEST_TIMEOUT
                ),
                explicit=(
                    setting_value(settings, "stream_read_timeout")
                    if setting_value(settings, "stream_read_timeout") is not MISSING
                    else None
                ),
            ),
            max_request_body_bytes=as_int(
                setting_value(settings, "max_request_body_bytes"),
                DEFAULT_MAX_REQUEST_BODY_BYTES,
            ),
            reasoning_content_path=as_path(
                setting_value(settings, "reasoning_content_path"),
                default_reasoning_content_path(),
                config_dir,
            ),
            missing_reasoning_strategy=normalize_missing_reasoning_strategy(
                setting_value(settings, "missing_reasoning_strategy")
            ),
            reasoning_cache_max_age_seconds=as_int(
                setting_value(settings, "reasoning_cache_max_age_seconds"),
                DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS,
            ),
            display_reasoning=as_bool(
                setting_value(settings, "display_reasoning"),
                DEFAULT_DISPLAY_REASONING,
            ),
            collapsible_reasoning=as_bool(
                setting_value(settings, "collapsible_reasoning"),
                DEFAULT_COLLAPSIBLE_REASONING,
            ),
            cors=as_bool(
                setting_value(settings, "cors"),
                DEFAULT_CORS,
            ),
            ollama=as_bool(
                setting_value(settings, "ollama"),
                True,
            ),
            verbose=as_bool(
                setting_value(settings, "verbose"),
                DEFAULT_VERBOSE,
            ),
            compact=as_bool(
                setting_value(settings, "compact"),
                False,
            ),
            ngrok=as_bool(
                setting_value(settings, "ngrok"),
                DEFAULT_NGROK,
            ),
            ngrok_health_check_interval=as_float(
                setting_value(settings, "ngrok_health_check_interval"),
                DEFAULT_NGROK_HEALTH_CHECK_INTERVAL,
            ),
            max_pool_connections=_auto_pool_connections(
                as_int(
                    setting_value(settings, "max_thread_pool"), DEFAULT_MAX_THREAD_POOL
                ),
                explicit=(
                    setting_value(settings, "max_pool_connections")
                    if setting_value(settings, "max_pool_connections") is not MISSING
                    else None
                ),
            ),
            max_thread_pool=as_int(
                setting_value(settings, "max_thread_pool"),
                DEFAULT_MAX_THREAD_POOL,
            ),
            max_queue_size=_auto_queue_size(
                as_int(setting_value(settings, "max_thread_pool"), DEFAULT_MAX_THREAD_POOL)
            ),
            log_dir=(
                Path(v)
                if (v := setting_value(settings, "log_dir")) is not MISSING and v
                else default_log_dir()
            ),
        )
