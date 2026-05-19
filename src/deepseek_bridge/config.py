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
DEFAULT_UPSTREAM_RETRY_ATTEMPTS = 2
DEFAULT_UPSTREAM_RETRY_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_UPSTREAM_RETRY_MAX_DELAY_SECONDS = 4.0
DEFAULT_UPSTREAM_RETRY_JITTER_SECONDS = 0.25
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
KUBERNETES_REASONING_CONTENT_PATH = ":memory:"
DEFAULT_STORAGE_BACKEND = "sqlite"
DEFAULT_VALKEY_KEY_PREFIX = "deepseek-bridge"
DEFAULT_LOG_FORMAT = "text"
DEFAULT_TRACE_MODE = "metadata-only"
SUPPORTED_TRACE_MODES = {"metadata-only", "redacted", "full"}
DEFAULT_METRICS_ENABLED = False

CORS_ALLOWED_ORIGINS_TEXT = "\n".join(
    f"    - {origin}" for origin in DEFAULT_CORS_ALLOWED_ORIGINS
)
DEFAULT_CONFIG_HEADER = (
    "# This file was created automatically at ~/.deepseek-bridge/config.yaml."
)
_DEFAULT_RETRY_INITIAL_DELAY = DEFAULT_UPSTREAM_RETRY_INITIAL_DELAY_SECONDS
_DEFAULT_RETRY_MAX_DELAY = DEFAULT_UPSTREAM_RETRY_MAX_DELAY_SECONDS
_DEFAULT_RETRY_JITTER = DEFAULT_UPSTREAM_RETRY_JITTER_SECONDS
DEFAULT_CONFIG_TEXT = f"""{DEFAULT_CONFIG_HEADER}
# API keys are read from Cursor's Authorization header and forwarded upstream.
version: 1

runtime:
  mode: {DEFAULT_RUNTIME_MODE}

server:
  host: {DEFAULT_HOST}
  port: {DEFAULT_PORT}

upstream:
  base_url: {DEFAULT_UPSTREAM_BASE_URL}
  model: {DEFAULT_UPSTREAM_MODEL}
  thinking:
    mode: {DEFAULT_THINKING}
    reasoning_effort: {DEFAULT_REASONING_EFFORT}

storage:
  backend: sqlite
  sqlite:
    path: {REASONING_CONTENT_FILE_NAME}

reasoning_cache:
  max_age_seconds: {DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS}
  # max_entries: null  # auto-sized from available disk space
  missing_reasoning_strategy: {DEFAULT_MISSING_REASONING_STRATEGY}

reasoning_display:
  enabled: {str(DEFAULT_DISPLAY_REASONING).lower()}
  collapsible: {str(DEFAULT_COLLAPSIBLE_REASONING).lower()}

logging:
  level: info
  format: text
  trace_mode: metadata-only
  file:
    enabled: true
    path: null  # auto: ~/.deepseek-bridge/logs

metrics:
  enabled: false

cors:
  enabled: {str(DEFAULT_CORS).lower()}
  allowed_origins:
{CORS_ALLOWED_ORIGINS_TEXT}
  allow_credentials: {str(DEFAULT_CORS_ALLOW_CREDENTIALS).lower()}

ollama:
  enabled: true

performance:
  request_timeout: {DEFAULT_REQUEST_TIMEOUT:g}
  stream_read_timeout: {DEFAULT_STREAM_READ_TIMEOUT:g}
  upstream_retry_attempts: {DEFAULT_UPSTREAM_RETRY_ATTEMPTS}
  upstream_retry_initial_delay_seconds: {_DEFAULT_RETRY_INITIAL_DELAY:g}
  upstream_retry_max_delay_seconds: {_DEFAULT_RETRY_MAX_DELAY:g}
  upstream_retry_jitter_seconds: {_DEFAULT_RETRY_JITTER:g}
  max_request_body_bytes: {DEFAULT_MAX_REQUEST_BODY_BYTES}
  max_pool_connections: {DEFAULT_MAX_POOL_CONNECTIONS}
  max_thread_pool: {DEFAULT_MAX_THREAD_POOL}
"""

ENV_CONFIG_PATH = "DEEPSEEK_BRIDGE_CONFIG_PATH"


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


def as_optional_positive_int(value: Any) -> int | None:
    if value is MISSING or value is None or value == "":
        return None
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed > 0 else None


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


def _strict_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} must be a boolean "
        f"({', '.join(sorted(TRUE_VALUES | FALSE_VALUES))}); got {value!r}"
    )


def _strict_int(
    name: str,
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer; got {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {parsed!r}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be <= {maximum}; got {parsed!r}")
    return parsed


def _strict_float(
    name: str, value: Any, *, minimum: float | None = None
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number; got {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum:g}; got {parsed!r}")
    return parsed


def _strict_enum(name: str, value: Any, allowed: set[str]) -> str:
    normalized = str(value).strip().lower()
    if normalized in allowed:
        return normalized
    choices = ", ".join(sorted(allowed))
    raise ValueError(f"{name} must be one of {choices}; got {value!r}")


def _strict_thinking(name: str, value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"enabled", "disabled"}:
        return normalized
    if normalized in TRUE_VALUES:
        return "enabled"
    if normalized in FALSE_VALUES:
        return "disabled"
    raise ValueError(f"{name} must be enabled or disabled; got {value!r}")


def _strict_str_tuple(name: str, value: Any) -> tuple[str, ...]:
    parsed = as_str_tuple(value, ())
    if parsed:
        return parsed
    raise ValueError(
        f"{name} must be a non-empty list or comma-separated string"
    )


def _strict_mapping(name: str, value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise ValueError(f"{name} must be a mapping; got {type(value).__name__}")


def _optional_mapping(
    settings: Mapping[str, Any], key: str
) -> Mapping[str, Any] | None:
    value = setting_value(settings, key)
    if value is MISSING or value is None:
        return None
    return _strict_mapping(key, value)


def _env_path(value: Any) -> Path | str:
    if str(value) == ":memory:":
        return ":memory:"
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _config_path(name: str, value: Any, config_dir: Path) -> Path | str:
    if value is None or value == "":
        raise ValueError(f"{name} must not be empty")
    return as_path(value, Path(), config_dir)


def _set_if_present(
    target: dict[str, Any],
    source: Mapping[str, Any],
    source_key: str,
    target_key: str,
    converter: Any = None,
    *,
    name: str | None = None,
) -> None:
    value = setting_value(source, source_key)
    if value is MISSING:
        return
    setting_name = name or source_key
    target[target_key] = converter(setting_name, value) if converter else value


def _validate_schema_version(settings: Mapping[str, Any]) -> None:
    version = setting_value(settings, "version")
    if version is MISSING or version is None:
        return
    parsed = _strict_int("version", version, minimum=1)
    if parsed != 1:
        raise ValueError(f"Unsupported config version {parsed}; expected 1")


def normalize_config_settings(
    settings: Mapping[str, Any], config_dir: Path
) -> dict[str, Any]:
    """Map structured schema v1 YAML onto the flat runtime settings."""

    _validate_schema_version(settings)
    normalized = dict(settings)

    def to_str(_name: str, value: Any) -> str:
        return str(value)

    if runtime := _optional_mapping(settings, "runtime"):
        _set_if_present(
            normalized,
            runtime,
            "mode",
            "runtime_mode",
            lambda n, v: _strict_enum(
                n, v, {DEFAULT_RUNTIME_MODE, KUBERNETES_RUNTIME_MODE}
            ),
            name="runtime.mode",
        )

    if server := _optional_mapping(settings, "server"):
        _set_if_present(
            normalized, server, "host", "host", to_str, name="server.host"
        )
        _set_if_present(
            normalized,
            server,
            "port",
            "port",
            lambda n, v: _strict_int(n, v, minimum=1, maximum=65535),
            name="server.port",
        )

    if upstream := _optional_mapping(settings, "upstream"):
        _set_if_present(
            normalized,
            upstream,
            "base_url",
            "base_url",
            to_str,
            name="upstream.base_url",
        )
        _set_if_present(
            normalized,
            upstream,
            "model",
            "model",
            to_str,
            name="upstream.model",
        )
        thinking = setting_value(upstream, "thinking")
        if isinstance(thinking, Mapping):
            _set_if_present(
                normalized,
                thinking,
                "mode",
                "thinking",
                _strict_thinking,
                name="upstream.thinking.mode",
            )
            _set_if_present(
                normalized,
                thinking,
                "reasoning_effort",
                "reasoning_effort",
                lambda n, v: _strict_enum(
                    n, v, {"low", "medium", "high", "max", "xhigh"}
                ),
                name="upstream.thinking.reasoning_effort",
            )
        elif thinking is not MISSING:
            normalized["thinking"] = _strict_thinking(
                "upstream.thinking", thinking
            )

    if storage := _optional_mapping(settings, "storage"):
        _set_if_present(
            normalized,
            storage,
            "backend",
            "storage_backend",
            lambda n, v: _strict_enum(n, v, {"sqlite", "valkey"}),
            name="storage.backend",
        )
        if sqlite := _optional_mapping(storage, "sqlite"):
            path = setting_value(sqlite, "path")
            if path is not MISSING:
                normalized["reasoning_content_path"] = _config_path(
                    "storage.sqlite.path", path, config_dir
                )
        if valkey := _optional_mapping(storage, "valkey"):
            _set_if_present(
                normalized,
                valkey,
                "url",
                "valkey_url",
                to_str,
                name="storage.valkey.url",
            )
            _set_if_present(
                normalized,
                valkey,
                "key_prefix",
                "valkey_key_prefix",
                to_str,
                name="storage.valkey.key_prefix",
            )

    if reasoning_cache := _optional_mapping(settings, "reasoning_cache"):
        _set_if_present(
            normalized,
            reasoning_cache,
            "max_age_seconds",
            "reasoning_cache_max_age_seconds",
            lambda n, v: _strict_int(n, v, minimum=1),
            name="reasoning_cache.max_age_seconds",
        )
        max_entries = setting_value(reasoning_cache, "max_entries")
        if max_entries is not MISSING and max_entries is not None:
            normalized["reasoning_cache_max_entries"] = _strict_int(
                "reasoning_cache.max_entries", max_entries, minimum=1
            )
        _set_if_present(
            normalized,
            reasoning_cache,
            "missing_reasoning_strategy",
            "missing_reasoning_strategy",
            lambda n, v: _strict_enum(n, v, {"recover", "reject"}),
            name="reasoning_cache.missing_reasoning_strategy",
        )

    if reasoning_display := _optional_mapping(settings, "reasoning_display"):
        _set_if_present(
            normalized,
            reasoning_display,
            "enabled",
            "display_reasoning",
            _strict_bool,
            name="reasoning_display.enabled",
        )
        _set_if_present(
            normalized,
            reasoning_display,
            "collapsible",
            "collapsible_reasoning",
            _strict_bool,
            name="reasoning_display.collapsible",
        )

    if logging_settings := _optional_mapping(settings, "logging"):
        level = setting_value(logging_settings, "level")
        if level is not MISSING:
            normalized["debug"] = (
                _strict_enum("logging.level", level, {"debug", "info"})
                == "debug"
            )
        _set_if_present(
            normalized,
            logging_settings,
            "format",
            "log_format",
            lambda n, v: _strict_enum(n, v, {"text", "json"}),
            name="logging.format",
        )
        _set_if_present(
            normalized,
            logging_settings,
            "compact",
            "compact",
            _strict_bool,
            name="logging.compact",
        )
        _set_if_present(
            normalized,
            logging_settings,
            "trace_mode",
            "trace_mode",
            lambda n, v: _strict_enum(n, v, SUPPORTED_TRACE_MODES),
            name="logging.trace_mode",
        )
        trace_dir = setting_value(logging_settings, "trace_dir")
        if trace_dir is not MISSING and trace_dir is not None:
            normalized["trace_dir"] = _config_path(
                "logging.trace_dir", trace_dir, config_dir
            )
        if file_settings := _optional_mapping(logging_settings, "file"):
            _set_if_present(
                normalized,
                file_settings,
                "enabled",
                "log_file_enabled",
                _strict_bool,
                name="logging.file.enabled",
            )
            path = setting_value(file_settings, "path")
            if path is not MISSING and path is not None:
                normalized["log_dir"] = _config_path(
                    "logging.file.path", path, config_dir
                )

    if metrics := _optional_mapping(settings, "metrics"):
        _set_if_present(
            normalized,
            metrics,
            "enabled",
            "metrics_enabled",
            _strict_bool,
            name="metrics.enabled",
        )

    cors = setting_value(settings, "cors")
    if isinstance(cors, Mapping):
        _set_if_present(
            normalized,
            cors,
            "enabled",
            "cors",
            _strict_bool,
            name="cors.enabled",
        )
        _set_if_present(
            normalized,
            cors,
            "allowed_origins",
            "cors_allowed_origins",
            _strict_str_tuple,
            name="cors.allowed_origins",
        )
        _set_if_present(
            normalized,
            cors,
            "allow_credentials",
            "cors_allow_credentials",
            _strict_bool,
            name="cors.allow_credentials",
        )

    ollama = setting_value(settings, "ollama")
    if isinstance(ollama, Mapping):
        _set_if_present(
            normalized,
            ollama,
            "enabled",
            "ollama",
            _strict_bool,
            name="ollama.enabled",
        )

    if performance := _optional_mapping(settings, "performance"):
        _set_if_present(
            normalized,
            performance,
            "request_timeout",
            "request_timeout",
            lambda n, v: _strict_float(n, v, minimum=0.001),
            name="performance.request_timeout",
        )
        _set_if_present(
            normalized,
            performance,
            "stream_read_timeout",
            "stream_read_timeout",
            lambda n, v: _strict_float(n, v, minimum=0.001),
            name="performance.stream_read_timeout",
        )
        _set_if_present(
            normalized,
            performance,
            "upstream_retry_attempts",
            "upstream_retry_attempts",
            lambda n, v: _strict_int(n, v, minimum=0),
            name="performance.upstream_retry_attempts",
        )
        _set_if_present(
            normalized,
            performance,
            "upstream_retry_initial_delay_seconds",
            "upstream_retry_initial_delay_seconds",
            lambda n, v: _strict_float(n, v, minimum=0.0),
            name="performance.upstream_retry_initial_delay_seconds",
        )
        _set_if_present(
            normalized,
            performance,
            "upstream_retry_max_delay_seconds",
            "upstream_retry_max_delay_seconds",
            lambda n, v: _strict_float(n, v, minimum=0.0),
            name="performance.upstream_retry_max_delay_seconds",
        )
        _set_if_present(
            normalized,
            performance,
            "upstream_retry_jitter_seconds",
            "upstream_retry_jitter_seconds",
            lambda n, v: _strict_float(n, v, minimum=0.0),
            name="performance.upstream_retry_jitter_seconds",
        )
        _set_if_present(
            normalized,
            performance,
            "max_request_body_bytes",
            "max_request_body_bytes",
            lambda n, v: _strict_int(n, v, minimum=1),
            name="performance.max_request_body_bytes",
        )
        _set_if_present(
            normalized,
            performance,
            "max_pool_connections",
            "max_pool_connections",
            lambda n, v: _strict_int(n, v, minimum=1),
            name="performance.max_pool_connections",
        )
        _set_if_present(
            normalized,
            performance,
            "max_thread_pool",
            "max_thread_pool",
            lambda n, v: _strict_int(n, v, minimum=1),
            name="performance.max_thread_pool",
        )

    return normalized


def settings_from_env(environ: Mapping[str, str] | None) -> dict[str, Any]:
    if environ is None:
        environ = os.environ
    settings: dict[str, Any] = {}

    string_vars = {
        "DEEPSEEK_BRIDGE_RUNTIME_MODE": "runtime_mode",
        "DEEPSEEK_BRIDGE_RUNTIME": "runtime_mode",
        "DEEPSEEK_BRIDGE_HOST": "host",
        "DEEPSEEK_BRIDGE_BASE_URL": "base_url",
        "DEEPSEEK_BRIDGE_UPSTREAM_BASE_URL": "base_url",
        "DEEPSEEK_BRIDGE_MODEL": "model",
        "DEEPSEEK_BRIDGE_UPSTREAM_MODEL": "model",
        "DEEPSEEK_BRIDGE_VALKEY_URL": "valkey_url",
        "DEEPSEEK_BRIDGE_VALKEY_KEY_PREFIX": "valkey_key_prefix",
    }
    for env_name, key in string_vars.items():
        if env_name in environ:
            settings[key] = environ[env_name]

    path_vars = {
        "DEEPSEEK_BRIDGE_REASONING_CONTENT_PATH": "reasoning_content_path",
        "DEEPSEEK_BRIDGE_SQLITE_PATH": "reasoning_content_path",
        "DEEPSEEK_BRIDGE_LOG_DIR": "log_dir",
        "DEEPSEEK_BRIDGE_TRACE_DIR": "trace_dir",
    }
    for env_name, key in path_vars.items():
        if env_name in environ:
            settings[key] = _env_path(environ[env_name])
            if key == "log_dir":
                settings["log_file_enabled"] = True

    int_vars = {
        "DEEPSEEK_BRIDGE_PORT": ("port", 1, 65535),
        "DEEPSEEK_BRIDGE_MAX_REQUEST_BODY_BYTES": (
            "max_request_body_bytes",
            1,
            None,
        ),
        "DEEPSEEK_BRIDGE_REASONING_CACHE_MAX_AGE_SECONDS": (
            "reasoning_cache_max_age_seconds",
            1,
            None,
        ),
        "DEEPSEEK_BRIDGE_REASONING_CACHE_MAX_ENTRIES": (
            "reasoning_cache_max_entries",
            1,
            None,
        ),
        "DEEPSEEK_BRIDGE_MAX_POOL_CONNECTIONS": (
            "max_pool_connections",
            1,
            None,
        ),
        "DEEPSEEK_BRIDGE_MAX_THREAD_POOL": ("max_thread_pool", 1, None),
        "DEEPSEEK_BRIDGE_UPSTREAM_RETRY_ATTEMPTS": (
            "upstream_retry_attempts",
            0,
            None,
        ),
    }
    for env_name, (key, minimum, maximum) in int_vars.items():
        if env_name in environ:
            settings[key] = _strict_int(
                env_name,
                environ[env_name],
                minimum=minimum,
                maximum=maximum,
            )

    float_vars = {
        "DEEPSEEK_BRIDGE_REQUEST_TIMEOUT": ("request_timeout", 0.001),
        "DEEPSEEK_BRIDGE_STREAM_READ_TIMEOUT": (
            "stream_read_timeout",
            0.001,
        ),
        "DEEPSEEK_BRIDGE_UPSTREAM_RETRY_INITIAL_DELAY_SECONDS": (
            "upstream_retry_initial_delay_seconds",
            0.0,
        ),
        "DEEPSEEK_BRIDGE_UPSTREAM_RETRY_MAX_DELAY_SECONDS": (
            "upstream_retry_max_delay_seconds",
            0.0,
        ),
        "DEEPSEEK_BRIDGE_UPSTREAM_RETRY_JITTER_SECONDS": (
            "upstream_retry_jitter_seconds",
            0.0,
        ),
    }
    for env_name, (key, float_minimum) in float_vars.items():
        if env_name in environ:
            settings[key] = _strict_float(
                env_name, environ[env_name], minimum=float_minimum
            )

    bool_vars = {
        "DEEPSEEK_BRIDGE_DISPLAY_REASONING": "display_reasoning",
        "DEEPSEEK_BRIDGE_COLLAPSIBLE_REASONING": "collapsible_reasoning",
        "DEEPSEEK_BRIDGE_DEBUG": "debug",
        "DEEPSEEK_BRIDGE_COMPACT": "compact",
        "DEEPSEEK_BRIDGE_CORS": "cors",
        "DEEPSEEK_BRIDGE_CORS_ENABLED": "cors",
        "DEEPSEEK_BRIDGE_CORS_ALLOW_CREDENTIALS": "cors_allow_credentials",
        "DEEPSEEK_BRIDGE_OLLAMA": "ollama",
        "DEEPSEEK_BRIDGE_OLLAMA_ENABLED": "ollama",
        "DEEPSEEK_BRIDGE_LOG_FILE_ENABLED": "log_file_enabled",
        "DEEPSEEK_BRIDGE_METRICS_ENABLED": "metrics_enabled",
    }
    for env_name, key in bool_vars.items():
        if env_name in environ:
            settings[key] = _strict_bool(env_name, environ[env_name])

    if "DEEPSEEK_BRIDGE_CORS_ALLOWED_ORIGINS" in environ:
        settings["cors_allowed_origins"] = _strict_str_tuple(
            "DEEPSEEK_BRIDGE_CORS_ALLOWED_ORIGINS",
            environ["DEEPSEEK_BRIDGE_CORS_ALLOWED_ORIGINS"],
        )

    if "DEEPSEEK_BRIDGE_THINKING" in environ:
        settings["thinking"] = _strict_thinking(
            "DEEPSEEK_BRIDGE_THINKING", environ["DEEPSEEK_BRIDGE_THINKING"]
        )
    if "DEEPSEEK_BRIDGE_REASONING_EFFORT" in environ:
        settings["reasoning_effort"] = _strict_enum(
            "DEEPSEEK_BRIDGE_REASONING_EFFORT",
            environ["DEEPSEEK_BRIDGE_REASONING_EFFORT"],
            {"low", "medium", "high", "max", "xhigh"},
        )
    if "DEEPSEEK_BRIDGE_MISSING_REASONING_STRATEGY" in environ:
        settings["missing_reasoning_strategy"] = _strict_enum(
            "DEEPSEEK_BRIDGE_MISSING_REASONING_STRATEGY",
            environ["DEEPSEEK_BRIDGE_MISSING_REASONING_STRATEGY"],
            {"recover", "reject"},
        )
    if "DEEPSEEK_BRIDGE_STORAGE_BACKEND" in environ:
        settings["storage_backend"] = _strict_enum(
            "DEEPSEEK_BRIDGE_STORAGE_BACKEND",
            environ["DEEPSEEK_BRIDGE_STORAGE_BACKEND"],
            {"sqlite", "valkey"},
        )
    if "DEEPSEEK_BRIDGE_LOG_LEVEL" in environ:
        settings["debug"] = (
            _strict_enum(
                "DEEPSEEK_BRIDGE_LOG_LEVEL",
                environ["DEEPSEEK_BRIDGE_LOG_LEVEL"],
                {"debug", "info"},
            )
            == "debug"
        )
    if "DEEPSEEK_BRIDGE_LOG_FORMAT" in environ:
        settings["log_format"] = _strict_enum(
            "DEEPSEEK_BRIDGE_LOG_FORMAT",
            environ["DEEPSEEK_BRIDGE_LOG_FORMAT"],
            {"text", "json"},
        )
    if "DEEPSEEK_BRIDGE_TRACE_MODE" in environ:
        settings["trace_mode"] = _strict_enum(
            "DEEPSEEK_BRIDGE_TRACE_MODE",
            environ["DEEPSEEK_BRIDGE_TRACE_MODE"],
            SUPPORTED_TRACE_MODES,
        )

    return settings


def validate_runtime_settings(settings: Mapping[str, Any]) -> None:
    storage_backend = (
        as_str(
            setting_value(settings, "storage_backend"), DEFAULT_STORAGE_BACKEND
        )
        .strip()
        .lower()
    )
    if storage_backend not in {"sqlite", "valkey"}:
        raise ValueError(
            "storage backend must be sqlite or valkey; "
            "set storage.backend or DEEPSEEK_BRIDGE_STORAGE_BACKEND"
        )
    if storage_backend == "valkey":
        valkey_url = as_str(setting_value(settings, "valkey_url"), "").strip()
        if not valkey_url:
            raise ValueError(
                "storage backend 'valkey' requires storage.valkey.url "
                "or DEEPSEEK_BRIDGE_VALKEY_URL"
            )
        valkey_key_prefix = (
            as_str(
                setting_value(settings, "valkey_key_prefix"),
                DEFAULT_VALKEY_KEY_PREFIX,
            )
            .strip()
            .strip(":")
        )
        if not valkey_key_prefix:
            raise ValueError(
                "storage backend 'valkey' requires a non-empty "
                "storage.valkey.key_prefix"
            )


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


def normalize_trace_mode(value: Any) -> str:
    trace_mode = as_str(value, DEFAULT_TRACE_MODE).strip().lower()
    if trace_mode in SUPPORTED_TRACE_MODES:
        return trace_mode
    return DEFAULT_TRACE_MODE


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


def _runtime_mode_from_environment(
    environ: Mapping[str, str],
) -> str | None:
    for env_key in ("DEEPSEEK_BRIDGE_RUNTIME_MODE", "DEEPSEEK_BRIDGE_RUNTIME"):
        if env_key in environ:
            return environ[env_key]
    return None


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
    upstream_retry_attempts: int = DEFAULT_UPSTREAM_RETRY_ATTEMPTS
    upstream_retry_initial_delay_seconds: float = (
        DEFAULT_UPSTREAM_RETRY_INITIAL_DELAY_SECONDS
    )
    upstream_retry_max_delay_seconds: float = (
        DEFAULT_UPSTREAM_RETRY_MAX_DELAY_SECONDS
    )
    upstream_retry_jitter_seconds: float = DEFAULT_UPSTREAM_RETRY_JITTER_SECONDS
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES
    reasoning_content_path: Path | str = field(
        default_factory=default_reasoning_content_path
    )
    missing_reasoning_strategy: str = DEFAULT_MISSING_REASONING_STRATEGY
    reasoning_cache_max_age_seconds: int = (
        DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS
    )
    reasoning_cache_max_entries: int | None = None
    display_reasoning: bool = DEFAULT_DISPLAY_REASONING
    collapsible_reasoning: bool = DEFAULT_COLLAPSIBLE_REASONING
    storage_backend: str = DEFAULT_STORAGE_BACKEND
    valkey_url: str = ""
    valkey_key_prefix: str = DEFAULT_VALKEY_KEY_PREFIX
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
    compact: bool = False
    trace_dir: Path | None = None
    trace_mode: str = DEFAULT_TRACE_MODE
    log_dir: Path | None = field(default_factory=default_log_dir)
    log_format: str = DEFAULT_LOG_FORMAT
    metrics_enabled: bool = DEFAULT_METRICS_ENABLED

    @classmethod
    def from_file(
        cls: type[ProxyConfig],
        config_path: str | Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        runtime_mode: str | None = None,
    ) -> ProxyConfig:
        return cls.from_sources(
            config_path=config_path,
            environ=environ,
            runtime_mode=runtime_mode,
        )

    @classmethod
    def from_sources(
        cls: type[ProxyConfig],
        config_path: str | Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        runtime_mode: str | None = None,
    ) -> ProxyConfig:
        environ = os.environ if environ is None else environ
        if config_path is None and (
            env_config_path := environ.get(ENV_CONFIG_PATH)
        ):
            config_path = env_config_path
        initial_runtime_mode = normalize_runtime_mode(
            runtime_mode
            if runtime_mode is not None
            else _runtime_mode_from_environment(environ)
        )
        settings, resolved_config_path = settings_from_config(
            config_path,
            populate_default=initial_runtime_mode != KUBERNETES_RUNTIME_MODE,
        )
        config_dir = resolved_config_path.parent
        settings = normalize_config_settings(settings, config_dir)
        settings.update(settings_from_env(environ))
        if runtime_mode is not None:
            settings["runtime_mode"] = normalize_runtime_mode(runtime_mode)
        validate_runtime_settings(settings)

        normalized_runtime_mode = normalize_runtime_mode(
            setting_value(settings, "runtime_mode")
        )
        kubernetes_mode = normalized_runtime_mode == KUBERNETES_RUNTIME_MODE
        host_default = KUBERNETES_HOST if kubernetes_mode else DEFAULT_HOST
        reasoning_content_default: Path | str = (
            KUBERNETES_REASONING_CONTENT_PATH
            if kubernetes_mode
            else default_reasoning_content_path()
        )
        log_dir_default = None if kubernetes_mode else default_log_dir()

        request_timeout = as_float(
            setting_value(settings, "request_timeout"), DEFAULT_REQUEST_TIMEOUT
        )
        stream_read_timeout = _auto_stream_timeout(
            request_timeout,
            explicit=(
                setting_value(settings, "stream_read_timeout")
                if setting_value(settings, "stream_read_timeout") is not MISSING
                else None
            ),
        )
        max_thread_pool = as_int(
            setting_value(settings, "max_thread_pool"), DEFAULT_MAX_THREAD_POOL
        )
        max_pool_connections = _auto_pool_connections(
            max_thread_pool,
            explicit=(
                setting_value(settings, "max_pool_connections")
                if setting_value(settings, "max_pool_connections")
                is not MISSING
                else None
            ),
        )
        log_file_enabled = as_bool(
            setting_value(settings, "log_file_enabled"), True
        )
        log_dir_value = setting_value(settings, "log_dir")
        log_dir = None
        if log_file_enabled:
            log_dir = as_optional_path(
                log_dir_value,
                log_dir_default,
                config_dir,
            )

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
            request_timeout=request_timeout,
            stream_read_timeout=stream_read_timeout,
            upstream_retry_attempts=as_int(
                setting_value(settings, "upstream_retry_attempts"),
                DEFAULT_UPSTREAM_RETRY_ATTEMPTS,
            ),
            upstream_retry_initial_delay_seconds=as_float(
                setting_value(settings, "upstream_retry_initial_delay_seconds"),
                DEFAULT_UPSTREAM_RETRY_INITIAL_DELAY_SECONDS,
            ),
            upstream_retry_max_delay_seconds=as_float(
                setting_value(settings, "upstream_retry_max_delay_seconds"),
                DEFAULT_UPSTREAM_RETRY_MAX_DELAY_SECONDS,
            ),
            upstream_retry_jitter_seconds=as_float(
                setting_value(settings, "upstream_retry_jitter_seconds"),
                DEFAULT_UPSTREAM_RETRY_JITTER_SECONDS,
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
            reasoning_cache_max_entries=as_optional_positive_int(
                setting_value(settings, "reasoning_cache_max_entries")
            ),
            display_reasoning=as_bool(
                setting_value(settings, "display_reasoning"),
                DEFAULT_DISPLAY_REASONING,
            ),
            collapsible_reasoning=as_bool(
                setting_value(settings, "collapsible_reasoning"),
                DEFAULT_COLLAPSIBLE_REASONING,
            ),
            storage_backend=as_str(
                setting_value(settings, "storage_backend"),
                DEFAULT_STORAGE_BACKEND,
            )
            .strip()
            .lower(),
            valkey_url=as_str(setting_value(settings, "valkey_url"), ""),
            valkey_key_prefix=as_str(
                setting_value(settings, "valkey_key_prefix"),
                DEFAULT_VALKEY_KEY_PREFIX,
            )
            .strip()
            .strip(":"),
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
            max_pool_connections=max_pool_connections,
            max_thread_pool=max_thread_pool,
            max_queue_size=_auto_queue_size(max_thread_pool),
            trace_dir=as_optional_path(
                setting_value(settings, "trace_dir"),
                None,
                config_dir,
            ),
            trace_mode=normalize_trace_mode(
                setting_value(settings, "trace_mode")
            ),
            log_dir=log_dir,
            log_format=as_str(
                setting_value(settings, "log_format"), DEFAULT_LOG_FORMAT
            )
            .strip()
            .lower(),
            metrics_enabled=as_bool(
                setting_value(settings, "metrics_enabled"),
                DEFAULT_METRICS_ENABLED,
            ),
        )
