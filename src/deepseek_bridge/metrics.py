from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

LabelSet = tuple[tuple[str, str], ...]
MetricKey = tuple[str, LabelSet]


@dataclass
class _Summary:
    count: int = 0
    total: float = 0.0


def _labels(values: Mapping[str, object] | None = None) -> LabelSet:
    if not values:
        return ()
    return tuple(
        sorted((str(key), str(value)) for key, value in values.items())
    )


def _escape_label_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _format_labels(labels: LabelSet) -> str:
    if not labels:
        return ""
    body = ",".join(
        f'{name}="{_escape_label_value(value)}"' for name, value in labels
    )
    return f"{{{body}}}"


def _format_number(value: float) -> str:
    if not math.isfinite(value):
        return "0"
    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


def normalize_http_path(path: str) -> str:
    """Return a low-cardinality path label for metrics."""
    known_paths = {
        "/api/show",
        "/api/tags",
        "/api/version",
        "/chat/completions",
        "/completions",
        "/embeddings",
        "/health",
        "/healthz",
        "/metrics",
        "/models",
        "/readyz",
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/embeddings",
        "/v1/health",
        "/v1/healthz",
        "/v1/metrics",
        "/v1/models",
        "/v1/readyz",
    }
    if path in known_paths:
        return path
    return "unknown"


def normalize_model_label(model: str) -> str:
    if model in {"deepseek-v4-pro", "deepseek-v4-flash"}:
        return model
    if model.startswith("deepseek-"):
        return "deepseek-other"
    return "custom"


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[MetricKey, float] = {}
        self._summaries: dict[MetricKey, _Summary] = {}
        self._streams_active = 0

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._summaries.clear()
            self._streams_active = 0

    def inc_counter(
        self,
        name: str,
        labels: Mapping[str, object] | None = None,
        amount: float = 1.0,
    ) -> None:
        key = (name, _labels(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def observe_summary(
        self,
        name: str,
        value: float,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        key = (name, _labels(labels))
        safe_value = value if math.isfinite(value) and value >= 0 else 0.0
        with self._lock:
            summary = self._summaries.setdefault(key, _Summary())
            summary.count += 1
            summary.total += safe_value

    def record_http_request(
        self,
        *,
        method: str,
        path: str,
        status: int | str,
        duration_seconds: float,
    ) -> None:
        status_label = str(status)
        path_label = normalize_http_path(path)
        self.inc_counter(
            "deepseek_bridge_http_requests_total",
            {
                "method": method.upper(),
                "path": path_label,
                "status": status_label,
            },
        )
        self.observe_summary(
            "deepseek_bridge_http_request_duration_seconds",
            duration_seconds,
            {"path": path_label, "status": status_label},
        )

    def record_upstream_request(
        self,
        *,
        model: str,
        status: int | str,
        duration_seconds: float,
    ) -> None:
        model_label = normalize_model_label(model or "custom")
        labels = {"model": model_label, "status": str(status)}
        self.inc_counter("deepseek_bridge_upstream_requests_total", labels)
        self.observe_summary(
            "deepseek_bridge_upstream_request_duration_seconds",
            duration_seconds,
            {"model": model_label},
        )

    def stream_started(self) -> None:
        with self._lock:
            self._streams_active += 1

    def stream_finished(self) -> None:
        with self._lock:
            self._streams_active = max(0, self._streams_active - 1)

    def record_cache_hit(self, backend: str) -> None:
        self.inc_counter(
            "deepseek_bridge_cache_hits_total", {"backend": backend}
        )

    def record_cache_miss(self, backend: str) -> None:
        self.inc_counter(
            "deepseek_bridge_cache_misses_total", {"backend": backend}
        )

    def observe_storage_operation(
        self, *, backend: str, operation: str, duration_seconds: float
    ) -> None:
        self.observe_summary(
            "deepseek_bridge_storage_operation_duration_seconds",
            duration_seconds,
            {"backend": backend, "operation": operation},
        )

    def record_storage_error(self, *, backend: str, operation: str) -> None:
        self.inc_counter(
            "deepseek_bridge_storage_errors_total",
            {"backend": backend, "operation": operation},
        )

    def render_prometheus(self, server: Any | None = None) -> str:
        with self._lock:
            counters = dict(self._counters)
            summaries = {
                key: _Summary(value.count, value.total)
                for key, value in self._summaries.items()
            }
            streams_active = self._streams_active

        lines: list[str] = []
        self._emit_counter(
            lines,
            counters,
            "deepseek_bridge_http_requests_total",
            "Inbound HTTP requests by method, normalized path, and status.",
        )
        self._emit_summary(
            lines,
            summaries,
            "deepseek_bridge_http_request_duration_seconds",
            "Inbound HTTP request duration in seconds.",
        )
        self._emit_counter(
            lines,
            counters,
            "deepseek_bridge_upstream_requests_total",
            "Upstream DeepSeek requests by model and status.",
        )
        self._emit_summary(
            lines,
            summaries,
            "deepseek_bridge_upstream_request_duration_seconds",
            "Upstream DeepSeek request duration in seconds.",
        )
        self._emit_gauge(
            lines,
            "deepseek_bridge_streams_active",
            "Currently active streaming responses.",
            float(streams_active),
        )
        self._emit_thread_pool_gauges(lines, server)
        self._emit_counter(
            lines,
            counters,
            "deepseek_bridge_cache_hits_total",
            "Reasoning cache lookup hits by backend.",
        )
        self._emit_counter(
            lines,
            counters,
            "deepseek_bridge_cache_misses_total",
            "Reasoning cache lookup misses by backend.",
        )
        self._emit_cache_hit_ratio(lines, counters)
        self._emit_summary(
            lines,
            summaries,
            "deepseek_bridge_storage_operation_duration_seconds",
            "Reasoning storage operation duration in seconds.",
        )
        self._emit_counter(
            lines,
            counters,
            "deepseek_bridge_storage_errors_total",
            "Reasoning storage operation errors by backend and operation.",
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _emit_counter(
        lines: list[str],
        counters: dict[MetricKey, float],
        name: str,
        help_text: str,
    ) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        samples = [
            (labels, value)
            for (metric_name, labels), value in counters.items()
            if metric_name == name
        ]
        for labels, value in sorted(samples):
            lines.append(
                f"{name}{_format_labels(labels)} {_format_number(value)}"
            )

    @staticmethod
    def _emit_summary(
        lines: list[str],
        summaries: dict[MetricKey, _Summary],
        name: str,
        help_text: str,
    ) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} summary")
        samples = [
            (labels, value)
            for (metric_name, labels), value in summaries.items()
            if metric_name == name
        ]
        for labels, value in sorted(samples):
            label_text = _format_labels(labels)
            lines.append(f"{name}_count{label_text} {value.count}")
            lines.append(
                f"{name}_sum{label_text} {_format_number(value.total)}"
            )

    @staticmethod
    def _emit_gauge(
        lines: list[str],
        name: str,
        help_text: str,
        value: float,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(
            f"{name}{_format_labels(_labels(labels))} {_format_number(value)}"
        )

    def _emit_thread_pool_gauges(
        self, lines: list[str], server: Any | None
    ) -> None:
        active = self._server_int(server, "active_worker_count")
        queue = self._server_int(server, "queue_size")
        self._emit_gauge(
            lines,
            "deepseek_bridge_thread_pool_active",
            "Active request handler worker count.",
            float(active),
        )
        self._emit_gauge(
            lines,
            "deepseek_bridge_thread_pool_queue",
            "Pending request handler queue size.",
            float(queue),
        )

    @staticmethod
    def _server_int(server: Any | None, attribute: str) -> int:
        if server is None:
            return 0
        try:
            return int(getattr(server, attribute))
        except Exception:
            return 0

    @staticmethod
    def _emit_cache_hit_ratio(
        lines: list[str], counters: dict[MetricKey, float]
    ) -> None:
        name = "deepseek_bridge_cache_hit_ratio"
        lines.append(f"# HELP {name} Reasoning cache hit ratio by backend.")
        lines.append(f"# TYPE {name} gauge")
        backends: set[str] = set()
        for metric_name, labels in counters:
            if metric_name not in {
                "deepseek_bridge_cache_hits_total",
                "deepseek_bridge_cache_misses_total",
            }:
                continue
            label_map = dict(labels)
            backend = label_map.get("backend")
            if backend:
                backends.add(backend)
        for backend in sorted(backends):
            hit_labels = _labels({"backend": backend})
            hits = counters.get(
                ("deepseek_bridge_cache_hits_total", hit_labels), 0.0
            )
            misses = counters.get(
                ("deepseek_bridge_cache_misses_total", hit_labels), 0.0
            )
            total = hits + misses
            ratio = hits / total if total > 0 else 0.0
            lines.append(
                f'{name}{{backend="{_escape_label_value(backend)}"}} '
                f"{_format_number(ratio)}"
            )


METRICS = MetricsRegistry()
