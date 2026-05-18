#!/usr/bin/env python3
"""Baseline SSE streaming performance benchmark.

Measures the hot path of the proxy's SSE streaming response pipeline:
parsing SSE lines, rewriting them (JSON parse -> mutate -> serialize),
and feeding them through the StreamAccumulator with display adapter.

This is the before/after comparison for optimization tasks 6, 7, 8.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from deepseek_bridge.reasoning_store import ReasoningStore, conversation_scope
from deepseek_bridge.streaming import (
    CursorReasoningDisplayAdapter,
    StreamAccumulator,
)
from deepseek_bridge.streaming._sse import SYSTEM_FINGERPRINT, sse_data

ORIGINAL_MODEL = "deepseek-chat"
SCOPE = conversation_scope([{"role": "user", "content": "hello"}])
PRIOR_MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]
RESPONSE_CONTEXTS: list[tuple[str, list[dict[str, Any]]]] = [
    (SCOPE, PRIOR_MESSAGES)
]

NUM_ITERATIONS = 5
BASELINE_PATH = Path(".sisyphus/evidence/baseline-sse-perf.txt")


def _chunk(
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-benchmark",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": ORIGINAL_MODEL,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def generate_synthetic_lines() -> list[bytes]:
    lines: list[bytes] = []

    lines.extend(
        sse_data(_chunk({"content": f"word_{i} "})) for i in range(699)
    )
    lines.append(sse_data(_chunk({"content": "final "}, finish_reason="stop")))
    lines.append(b"data: [DONE]\n\n")

    lines.extend(
        sse_data(_chunk({"reasoning_content": f"thinking_{i} "}))
        for i in range(199)
    )
    lines.append(
        sse_data(_chunk({"reasoning_content": "done "}, finish_reason="stop"))
    )
    lines.append(b"data: [DONE]\n\n")

    lines.append(
        sse_data(
            _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_bench_0",
                            "type": "function",
                            "function": {"name": "search", "arguments": ""},
                        }
                    ],
                }
            )
        )
    )
    lines.extend(
        (
            sse_data(
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": f'"arg_{i}"'},
                            }
                        ],
                    }
                )
            )
        )
        for i in range(98)
    )
    lines.append(sse_data(_chunk(delta={}, finish_reason="tool_calls")))
    lines.append(b"data: [DONE]\n\n")

    return lines


def process_all_lines(
    lines: list[bytes],
    original_model: str,
    accumulator: StreamAccumulator,
    display_adapter: CursorReasoningDisplayAdapter | None,
    reasoning_store: ReasoningStore,
    response_contexts: list[tuple[str, list[dict[str, Any]]]],
) -> list[bytes]:
    outputs: list[bytes] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            outputs.append(line)
            continue

        data_part = stripped[len(b"data:") :].strip()
        if data_part == b"[DONE]":
            for scope, prior_messages in response_contexts:
                accumulator.store_reasoning(
                    reasoning_store, scope, "", prior_messages
                )
            if display_adapter is not None:
                display_adapter.flush_chunk(original_model)
            outputs.append(b"data: [DONE]\n\n")
            continue

        try:
            chunk: Any = json.loads(data_part.decode("utf-8"))
        except json.JSONDecodeError, UnicodeDecodeError:
            outputs.append(line)
            continue

        if not isinstance(chunk, dict):
            outputs.append(line)
            continue

        accumulator.ingest_chunk(chunk)

        for scope, prior_messages in response_contexts:
            accumulator.store_ready_reasoning(
                reasoning_store, scope, "", prior_messages
            )

        if display_adapter is not None:
            display_adapter.rewrite_chunk(chunk)

        chunk["model"] = original_model
        chunk["system_fingerprint"] = SYSTEM_FINGERPRINT

        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
        outputs.append(
            b"data: "
            + json.dumps(
                chunk, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            + ending
        )

    return outputs


def run_benchmark() -> None:
    lines = generate_synthetic_lines()
    total_data_lines = sum(
        1
        for line in lines
        if line.strip().startswith(b"data:") and b"[DONE]" not in line
    )
    total_sse_lines = len(lines)

    times: list[float] = []

    print("=== SSE Streaming Performance Baseline ===\n")
    print(f"  Synthetic data lines: {total_data_lines}")
    print(f"  Total SSE lines (incl. [DONE]): {total_sse_lines}")
    print(f"  Iterations: {NUM_ITERATIONS}\n")

    for iteration in range(1, NUM_ITERATIONS + 1):
        accumulator = StreamAccumulator()
        display_adapter = CursorReasoningDisplayAdapter(collapsible=True)
        reasoning_store = ReasoningStore(":memory:")

        start = time.perf_counter()
        process_all_lines(
            lines,
            ORIGINAL_MODEL,
            accumulator,
            display_adapter,
            reasoning_store,
            RESPONSE_CONTEXTS,
        )
        elapsed = time.perf_counter() - start
        reasoning_store.close()

        times.append(elapsed)

        throughput = total_data_lines / elapsed
        latency_us = (elapsed / total_data_lines) * 1_000_000

        print(
            f"  Iteration {iteration}: "
            f"{elapsed:.4f}s total  ·  "
            f"{throughput:>8.0f} chunks/s  ·  "
            f"{latency_us:>7.2f} µs/chunk"
        )

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)
    avg_throughput = total_data_lines / avg_time
    avg_latency_us = (avg_time / total_data_lines) * 1_000_000

    summary = (
        f"Iterations:          {NUM_ITERATIONS}\n"
        f"Total data chunks:   {total_data_lines}\n"
        f"Total SSE lines:     {total_sse_lines}\n"
        f"\n"
        f"  Total time (avg):  {avg_time:.4f}s\n"
        f"  Total time (min):  {min_time:.4f}s\n"
        f"  Total time (max):  {max_time:.4f}s\n"
        f"  Throughput (avg):  {avg_throughput:>8.0f} chunks/s\n"
        f"  Latency   (avg):   {avg_latency_us:>7.2f} µs/chunk\n"
    )

    print("\n── Summary " + "─" * 46)
    print(summary)

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(summary)
    print(f"Baseline saved to {BASELINE_PATH}")


if __name__ == "__main__":
    run_benchmark()
