"""Regression tests for Prometheus metric rendering."""

from __future__ import annotations

import unittest

from deepseek_bridge.metrics import MetricsRegistry


class MetricsRegistryTests(unittest.TestCase):
    def test_duration_metrics_render_histogram_buckets(self) -> None:
        metrics = MetricsRegistry()

        metrics.record_http_request(
            method="POST",
            path="/v1/chat/completions",
            status=200,
            duration_seconds=0.2,
        )
        metrics.record_http_request(
            method="POST",
            path="/v1/chat/completions",
            status=200,
            duration_seconds=0.6,
        )
        metrics.record_upstream_request(
            model="deepseek-v4-pro",
            status=200,
            duration_seconds=1.2,
        )
        metrics.observe_storage_operation(
            backend="valkey",
            operation="get",
            duration_seconds=0.02,
        )

        body = metrics.render_prometheus()

        self.assertIn(
            "# TYPE deepseek_bridge_http_request_duration_seconds histogram",
            body,
        )
        self.assertIn(
            'deepseek_bridge_http_request_duration_seconds_bucket'
            '{le="0.1",path="/v1/chat/completions",status="200"} 0',
            body,
        )
        self.assertIn(
            'deepseek_bridge_http_request_duration_seconds_bucket'
            '{le="0.25",path="/v1/chat/completions",status="200"} 1',
            body,
        )
        self.assertIn(
            'deepseek_bridge_http_request_duration_seconds_bucket'
            '{le="1",path="/v1/chat/completions",status="200"} 2',
            body,
        )
        self.assertIn(
            'deepseek_bridge_http_request_duration_seconds_bucket'
            '{le="+Inf",path="/v1/chat/completions",status="200"} 2',
            body,
        )
        self.assertIn(
            'deepseek_bridge_http_request_duration_seconds_count'
            '{path="/v1/chat/completions",status="200"} 2',
            body,
        )
        self.assertIn(
            'deepseek_bridge_http_request_duration_seconds_sum'
            '{path="/v1/chat/completions",status="200"} 0.8',
            body,
        )
        self.assertIn(
            "# TYPE deepseek_bridge_upstream_request_duration_seconds "
            "histogram",
            body,
        )
        self.assertIn(
            'deepseek_bridge_upstream_request_duration_seconds_bucket'
            '{le="+Inf",model="deepseek-v4-pro"} 1',
            body,
        )
        self.assertIn(
            "# TYPE deepseek_bridge_storage_operation_duration_seconds "
            "histogram",
            body,
        )
        self.assertIn(
            'deepseek_bridge_storage_operation_duration_seconds_bucket'
            '{backend="valkey",le="+Inf",operation="get"} 1',
            body,
        )


if __name__ == "__main__":
    unittest.main()
