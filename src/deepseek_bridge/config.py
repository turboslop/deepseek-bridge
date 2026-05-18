from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

APP_DIR_NAME = ".deepseek-bridge"
CONFIG_FILE_NAME = "config.yaml"
REASONING_CONTENT_FILE_NAME = "reasoning_content.sqlite3"

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
MISSING = object()

MODEL_CREATED_TIMESTAMPS: dict[str, int] = {
    "deepseek-v4-pro": 1735689600,
    "deepseek-v4-flash": 1735689600,
}

# ── Ollama / Copilot metadata ──────────────────────────────

OLLAMA_FORMAT = "gguf"
OLLAMA_PARAMETER_SIZE = "7B"
OLLAMA_QUANTIZATION_LEVEL = "Q4_K_M"
OLLAMA_CONTEXT_LENGTH = 128000
OLLAMA_EMBEDDING_LENGTH = 2048
OLLAMA_MAX_OUTPUT_TOKENS = 384000
OLLAMA_MODEL_SIZE = 4109865159
OLLAMA_MODIFIED_AT = "2026-01-01T00:00:00.000Z"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000
DEFAULT_UPSTREAM_BASE_URL = "https://api.deepseek.com"
DEFAULT_UPSTREAM_MODEL = "deepseek-v4-pro"
DEFAULT_THINKING = "enabled"
DEFAULT_REASONING_EFFORT = "max"
DEFAULT_DISPLAY_REASONING = True
DEFAULT_COLLAPSIBLE_REASONING = True
DEFAULT_DEBUG = False
DEFAULT_REQUEST_TIMEOUT = 300.0
DEFAULT_STREAM_READ_TIMEOUT = 180.0
DEFAULT_MAX_REQUEST_BODY_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_POOL_CONNECTIONS = 10
DEFAULT_MAX_THREAD_POOL = max(os.cpu_count() or 4, 12)
DEFAULT_CORS = True
DEFAULT_CORS_ALLOWED_ORIGINS = (
    "http://localhost",
    "http://localhost:*",
    "https://localhost",
    "https://localhost:*",
    "http://127.0.0.1",
    "http://127.0.0.1:*",
    "https://127.0.0.1",
    "https://127.0.0.1:*",
    "http://[::1]",
    "http://[::1]:*",
    "https://[::1]",
    "https://[::1]:*",
)
DEFAULT_CORS_ALLOW_CREDENTIALS = True
DEFAULT_MISSING_REASONING_STRATEGY = "recover"
DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_REASONING_CACHE_DISK_MB = 500
DEFAULT_LOG_DIR = str(Path.home() / APP_DIR_NAME / "logs")
DEFAULT_RUNTIME_MODE = "local"
KUBERNETES_RUNTIME_MODE = "kubernetes"
KUBERNETES_HOST = "0.0.0.0"
KUBERNETES_TUNNEL = "none"
KUBERNETES_REASONING_CONTENT_PATH = ":memory:"

ENV_SETTING_KEYS: dict[str, tuple[str, ...]] = {
    "runtime_mode": (
        "DEEPSEEK_BRIDGE_RUNTIME_MODE",
        "DEEPSEEK_BRIDGE_RUNTIME",
    ),
    "host": ("DEEPSEEK_BRIDGE_HOST",),
    "port": ("DEEPSEEK_BRIDGE_PORT",),
    "base_url": (
        "DEEPSEEK_BRIDGE_BASE_URL",
        "DEEPSEEK_BRIDGE_UPSTREAM_BASE_URL",
    ),
    "model": (
        "DEEPSEEK_BRIDGE_MODEL",
        "DEEPSEEK_BRIDGE_UPSTREAM_MODEL",
    ),
    "thinking": ("DEEPSEEK_BRIDGE_THINKING",),
    "reasoning_effort": ("DEEPSEEK_BRIDGE_REASONING_EFFORT",),
    "display_reasoning": ("DEEPSEEK_BRIDGE_DISPLAY_REASONING",),
    "collapsible_reasoning": ("DEEPSEEK_BRIDGE_COLLAPSIBLE_REASONING",),
    "request_timeout": ("DEEPSEEK_BRIDGE_REQUEST_TIMEOUT",),
    "stream_read_timeout": ("DEEPSEEK_BRIDGE_STREAM_READ_TIMEOUT",),
    "max_request_body_bytes": ("DEEPSEEK_BRIDGE_MAX_REQUEST_BODY_BYTES",),
    "reasoning_content_path": ("DEEPSEEK_BRIDGE_REASONING_CONTENT_PATH",),
    "missing_reasoning_strategy": (
        "DEEPSEEK_BRIDGE_MISSING_REASONING_STRATEGY",
    ),
    "reasoning_cache_max_age_seconds": (
        "DEEPSEEK_BRIDGE_REASONING_CACHE_MAX_AGE_SECONDS",
    ),
    "cors": ("DEEPSEEK_BRIDGE_CORS",),
    "cors_allowed_origins": ("DEEPSEEK_BRIDGE_CORS_ALLOWED_ORIGINS",),
    "cors_allow_credentials": ("DEEPSEEK_BRIDGE_CORS_ALLOW_CREDENTIALS",),
    "ollama": ("DEEPSEEK_BRIDGE_OLLAMA",),
    "compact": ("DEEPSEEK_BRIDGE_COMPACT",),
    "debug": ("DEEPSEEK_BRIDGE_DEBUG",),
    "tunnel": ("DEEPSEEK_BRIDGE_TUNNEL",),
    "cf_url": ("DEEPSEEK_BRIDGE_CF_URL",),
    "cfd_tunnel_name": ("DEEPSEEK_BRIDGE_CFD_TUNNEL_NAME",),
    "ngrok_url": ("DEEPSEEK_BRIDGE_NGROK_URL",),
    "max_pool_connections": ("DEEPSEEK_BRIDGE_MAX_POOL_CONNECTIONS",),
    "max_thread_pool": ("DEEPSEEK_BRIDGE_MAX_THREAD_POOL",),
    "log_dir": ("DEEPSEEK_BRIDGE_LOG_DIR",),
    "trace_dir": ("DEEPSEEK_BRIDGE_TRACE_DIR",),
}

CORS_ALLOWED_ORIGINS_TEXT = "\n".join(
    f"  - {origin}" for origin in DEFAULT_CORS_ALLOWED_ORIGINS
)
DEFAULT_CONFIG_HEADER = (
    "# This file was created automatically at ~/.deepseek-bridge/config.yaml."
)
DEFAULT_CONFIG_TEXT = f"""{DEFAULT_CONFIG_HEADER}
# API keys are read from Cursor's Authorization header and forwarded upstream.

# Essential settings — these are the ones you'll most likely customize
runtime_mode: {DEFAULT_RUNTIME_MODE}
model: {DEFAULT_UPSTREAM_MODEL}
base_url: {DEFAULT_UPSTREAM_BASE_URL}
thinking: {DEFAULT_THINKING}
reasoning_effort: {DEFAULT_REASONING_EFFORT}
display_reasoning: {str(DEFAULT_DISPLAY_REASONING).lower()}
collapsible_reasoning: {str(DEFAULT_COLLAPSIBLE_REASONING).lower()}

host: {DEFAULT_HOST}
port: {DEFAULT_PORT}
tunnel: cloudflared
# cf_url: https://app.example.com  # required for cloudflared tunnel
debug: false
cors: {str(DEFAULT_CORS).lower()}
cors_allowed_origins:
{CORS_ALLOWED_ORIGINS_TEXT}
cors_allow_credentials: {str(DEFAULT_CORS_ALLOW_CREDENTIALS).lower()}
request_timeout: {DEFAULT_REQUEST_TIMEOUT:g}
max_request_body_bytes: {DEFAULT_MAX_REQUEST_BODY_BYTES}

# Advanced — defaults are fine for most users
# missing_reasoning_strategy: {DEFAULT_MISSING_REASONING_STRATEGY}
# reasoning_content_path: {REASONING_CONTENT_FILE_NAME}
#   auto: ~/.deepseek-bridge/reasoning_content.sqlite3
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
        raise ValueError(
            f"Invalid YAML config at {config_path}: {exc}"
        ) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ValueError(
            f"Config file must contain a YAML mapping: {config_path}"
        )
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
    except TypeError, ValueError:
        return default


def as_float(value: Any, default: float) -> float:
    if value is MISSING or value is None:
        return default
    try:
        return float(value)
    except TypeError, ValueError:
        return default


def _normalize_config_origin(value: object) -> str:
    origin = str(value).strip()
    if origin not in {"*", "null"}:
        origin = origin.rstrip("/")
    return origin


def as_str_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is MISSING or value is None:
        return default
    if isinstance(value, str):
        raw_items: Sequence[object] = value.split(",")
    elif isinstance(value, list | tuple):
        raw_items = list(value)
    else:
        return default
    origins = tuple(
        origin for raw in raw_items if (origin := _normalize_config_origin(raw))
    )
    return origins or default


def _is_disabled_path(value: Any) -> bool:
    return str(value).strip().lower() in {"none", "null", "false"}


def as_path(
    value: Any, default_path: Path | str, relative_base: Path
) -> Path | str:
    if value is MISSING or value is None or value == "":
        return default_path
    if str(value) == ":memory:":
        return ":memory:"
    candidate_path = Path(str(value)).expanduser()
    if candidate_path.is_absolute():
        return candidate_path
    return relative_base / candidate_path


def as_optional_path(
    value: Any,
    default_path: Path | None,
    relative_base: Path | None = None,
) -> Path | None:
    if value is MISSING or value is None or value == "":
        return default_path
    if _is_disabled_path(value):
        return None
    candidate_path = Path(str(value)).expanduser()
    if candidate_path.is_absolute() or relative_base is None:
        return candidate_path
    return relative_base / candidate_path


def settings_from_config(
    config_path: str | Path | None,
    *,
    populate_default: bool = True,
) -> tuple[dict[str, Any], Path]:
    resolved_config_path = resolve_config_path(config_path)
    if (
        populate_default
        and config_path is None
        and not resolved_config_path.exists()
    ):
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


def normalize_runtime_mode(value: Any) -> str:
    runtime_mode = as_str(value, DEFAULT_RUNTIME_MODE).strip().lower()
    if runtime_mode in {DEFAULT_RUNTIME_MODE, KUBERNETES_RUNTIME_MODE}:
        return runtime_mode
    return DEFAULT_RUNTIME_MODE


def _auto_stream_timeout(request_timeout: float, explicit: Any = None) -> float:
    if explicit is not None:
        return as_float(explicit, DEFAULT_STREAM_READ_TIMEOUT)
    return max(request_timeout * 0.6, 60.0)


def _auto_pool_connections(max_thread_pool: int, explicit: Any = None) -> int:
    if explicit is not None:
        return as_int(explicit, DEFAULT_MAX_POOL_CONNECTIONS)
    return max(max_thread_pool, 10)


def _auto_queue_size(max_thread_pool: int) -> int:
    """Auto-calculate queue size from thread pool count."""
    return max_thread_pool * 2 + 10


def _auto_cache_max_rows(
    disk_budget_mb: int = DEFAULT_REASONING_CACHE_DISK_MB,
    disk_usage_path: str | Path | None = None,
) -> int:
    """Auto-calculate max rows based on disk budget."""
    try:
        import shutil

        usage_path = Path(disk_usage_path or default_app_dir()).expanduser()
        while not usage_path.exists() and usage_path != usage_path.parent:
            usage_path = usage_path.parent
        available_gb = shutil.disk_usage(usage_path).free / (1024**3)
        budget_mb = min(disk_budget_mb, available_gb * 1024 * 0.05)
    except Exception as exc:
        from .logging import LOG

        LOG.warning("failed to check disk usage, using default budget: %s", exc)
        budget_mb = disk_budget_mb
    est_row_bytes = 1500  # avg ~1.5KB per row
    return max(int((budget_mb * 1024 * 1024) / est_row_bytes), 10000)


def _settings_from_environment(
    environ: Mapping[str, str],
) -> dict[str, str]:
    settings: dict[str, str] = {}
    for config_key, env_keys in ENV_SETTING_KEYS.items():
        for env_key in env_keys:
            if env_key in environ:
                settings[config_key] = environ[env_key]
                break
    return settings


def _runtime_mode_from_environment(
    environ: Mapping[str, str],
) -> str | None:
    for env_key in ENV_SETTING_KEYS["runtime_mode"]:
        if env_key in environ:
            return environ[env_key]
    return None


def _merged_settings(
    settings: Mapping[str, Any],
    environ: Mapping[str, str],
    cli_runtime_mode: str | None,
) -> tuple[dict[str, Any], str]:
    merged: dict[str, Any] = dict(settings)
    merged.update(_settings_from_environment(environ))
    if cli_runtime_mode is not None:
        merged["runtime_mode"] = cli_runtime_mode
    runtime_mode = normalize_runtime_mode(setting_value(merged, "runtime_mode"))
    merged["runtime_mode"] = runtime_mode
    return merged, runtime_mode


@dataclass(frozen=True)
class ProxyConfig:
    runtime_mode: str = DEFAULT_RUNTIME_MODE
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    upstream_base_url: str = DEFAULT_UPSTREAM_BASE_URL
    upstream_model: str = DEFAULT_UPSTREAM_MODEL
    thinking: str = DEFAULT_THINKING
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    stream_read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES
    reasoning_content_path: Path | str = field(
        default_factory=default_reasoning_content_path
    )
    missing_reasoning_strategy: str = DEFAULT_MISSING_REASONING_STRATEGY
    reasoning_cache_max_age_seconds: int = (
        DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS
    )
    display_reasoning: bool = DEFAULT_DISPLAY_REASONING
    collapsible_reasoning: bool = DEFAULT_COLLAPSIBLE_REASONING
    max_pool_connections: int = DEFAULT_MAX_POOL_CONNECTIONS
    max_thread_pool: int = DEFAULT_MAX_THREAD_POOL
    max_queue_size: int = field(
        default_factory=lambda: _auto_queue_size(DEFAULT_MAX_THREAD_POOL)
    )
    cors: bool = DEFAULT_CORS
    cors_allowed_origins: tuple[str, ...] = DEFAULT_CORS_ALLOWED_ORIGINS
    cors_allow_credentials: bool = DEFAULT_CORS_ALLOW_CREDENTIALS
    ollama: bool = True
    debug: bool = False
    tunnel: str = "cloudflared"
    cf_url: str = ""  # Cloudflare tunnel public URL
    cfd_tunnel_name: str = "deepseek-bridge"
    ngrok_url: str = ""  # Fixed ngrok endpoint URL (reserved domains)
    compact: bool = False
    trace_dir: Path | None = None
    log_dir: Path | None = field(default_factory=default_log_dir)

    @classmethod
    def from_file(
        cls: type[ProxyConfig],
        config_path: str | Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        runtime_mode: str | None = None,
    ) -> ProxyConfig:
        env = os.environ if environ is None else environ
        initial_runtime_mode = normalize_runtime_mode(
            runtime_mode
            if runtime_mode is not None
            else _runtime_mode_from_environment(env)
        )
        settings, resolved_config_path = settings_from_config(
            config_path,
            populate_default=initial_runtime_mode != KUBERNETES_RUNTIME_MODE,
        )
        config_dir = resolved_config_path.parent
        settings, normalized_runtime_mode = _merged_settings(
            settings, env, runtime_mode
        )
        kubernetes_mode = normalized_runtime_mode == KUBERNETES_RUNTIME_MODE
        host_default = KUBERNETES_HOST if kubernetes_mode else DEFAULT_HOST
        tunnel_default = KUBERNETES_TUNNEL if kubernetes_mode else "cloudflared"
        reasoning_content_default: Path | str = (
            KUBERNETES_REASONING_CONTENT_PATH
            if kubernetes_mode
            else default_reasoning_content_path()
        )
        log_dir_default = None if kubernetes_mode else default_log_dir()

        return cls(
            runtime_mode=normalized_runtime_mode,
            host=as_str(
                setting_value(settings, "host"),
                host_default,
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
                    setting_value(settings, "request_timeout"),
                    DEFAULT_REQUEST_TIMEOUT,
                ),
                explicit=(
                    setting_value(settings, "stream_read_timeout")
                    if setting_value(settings, "stream_read_timeout")
                    is not MISSING
                    else None
                ),
            ),
            max_request_body_bytes=as_int(
                setting_value(settings, "max_request_body_bytes"),
                DEFAULT_MAX_REQUEST_BODY_BYTES,
            ),
            reasoning_content_path=as_path(
                setting_value(settings, "reasoning_content_path"),
                reasoning_content_default,
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
            cors_allowed_origins=as_str_tuple(
                setting_value(settings, "cors_allowed_origins"),
                DEFAULT_CORS_ALLOWED_ORIGINS,
            ),
            cors_allow_credentials=as_bool(
                setting_value(settings, "cors_allow_credentials"),
                DEFAULT_CORS_ALLOW_CREDENTIALS,
            ),
            ollama=as_bool(
                setting_value(settings, "ollama"),
                True,
            ),
            compact=as_bool(
                setting_value(settings, "compact"),
                False,
            ),
            debug=as_bool(
                setting_value(settings, "debug"),
                False,
            ),
            tunnel=as_str(setting_value(settings, "tunnel"), tunnel_default),
            cf_url=as_str(setting_value(settings, "cf_url"), ""),
            cfd_tunnel_name=as_str(
                setting_value(settings, "cfd_tunnel_name"), "deepseek-bridge"
            ),
            ngrok_url=as_str(setting_value(settings, "ngrok_url"), ""),
            max_pool_connections=_auto_pool_connections(
                as_int(
                    setting_value(settings, "max_thread_pool"),
                    DEFAULT_MAX_THREAD_POOL,
                ),
                explicit=(
                    setting_value(settings, "max_pool_connections")
                    if setting_value(settings, "max_pool_connections")
                    is not MISSING
                    else None
                ),
            ),
            max_thread_pool=as_int(
                setting_value(settings, "max_thread_pool"),
                DEFAULT_MAX_THREAD_POOL,
            ),
            max_queue_size=_auto_queue_size(
                as_int(
                    setting_value(settings, "max_thread_pool"),
                    DEFAULT_MAX_THREAD_POOL,
                )
            ),
            log_dir=as_optional_path(
                setting_value(settings, "log_dir"),
                log_dir_default,
            ),
            trace_dir=as_optional_path(
                setting_value(settings, "trace_dir"),
                None,
                config_dir,
            ),
        )
