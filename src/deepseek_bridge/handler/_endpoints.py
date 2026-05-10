from __future__ import annotations

import hashlib
import json
import time

import urllib3

from .. import __version__

from .._types import RequestBodyTooLargeError, _error_body
from ..config import MODEL_CREATED_TIMESTAMPS
from ..config import (
    OLLAMA_CONTEXT_LENGTH,
    OLLAMA_EMBEDDING_LENGTH,
    OLLAMA_FORMAT,
    OLLAMA_MAX_OUTPUT_TOKENS,
    OLLAMA_MODEL_SIZE,
    OLLAMA_MODIFIED_AT,
    OLLAMA_PARAMETER_SIZE,
    OLLAMA_QUANTIZATION_LEVEL,
)
from ..helpers import _generate_request_id
from ..logging import LOG


class HandlerEndpoints:
    def _handle_embeddings_request(self) -> None:
        cursor_authorization = self._cursor_authorization()
        if cursor_authorization is None:
            LOG.warning("rejected embeddings request: missing bearer token")
            self._send_json(
                401,
                _error_body(
                    "Missing Authorization bearer token",
                    "authentication_error",
                    "invalid_api_key",
                ),
            )
            return
        try:
            payload = self._read_json_body()
        except (ValueError, RequestBodyTooLargeError) as exc:
            LOG.warning("rejected embeddings request: %s", exc)
            self._send_json(
                400,
                _error_body(str(exc), "invalid_request_error", "invalid_request_error"),
            )
            return

        model = str(payload.get("model") or self.config.upstream_model)
        upstream_body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        upstream_url = f"{self.config.upstream_base_url}/embeddings"

        try:
            response = self.upstream_pool.request(
                "POST",
                upstream_url,
                body=upstream_body,
                headers=self._upstream_headers(
                    stream=False, authorization=cursor_authorization
                ),
                preload_content=True,
                timeout=urllib3.Timeout(
                    connect=self.config.request_timeout,
                    read=self.config.request_timeout,
                ),
            )
            try:
                if response.status < 400:
                    body = response.data
                    headers = [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                    ]
                    self._send_response_headers(
                        response.status, headers, "sending embeddings response"
                    )
                    self._write_to_client(body, "sending embeddings body")
                else:
                    LOG.warning(
                        "embeddings endpoint not supported by upstream status=%s",
                        response.status,
                    )
                    self._send_json(
                        200,
                        {
                            "object": "list",
                            "data": [],
                            "model": model,
                            "usage": {"prompt_tokens": 0, "total_tokens": 0},
                        },
                    )
            finally:
                response.release_conn()
        except (urllib3.exceptions.HTTPError, OSError, ValueError) as exc:
            LOG.warning("embeddings request failed: %s", exc)
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [],
                    "model": model,
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                },
            )

    def _send_models(self) -> None:
        model_ids = list(
            dict.fromkeys(
                [
                    self.config.upstream_model,
                    "deepseek-v4-pro",
                    "deepseek-v4-flash",
                ]
            )
        )
        models = [
            {
                "id": model_id,
                "object": "model",
                "created": MODEL_CREATED_TIMESTAMPS.get(model_id, 1735689600),
                "owned_by": "deepseek",
            }
            for model_id in model_ids
        ]
        self._send_json(200, {"object": "list", "data": models})

    def _send_health(self) -> None:
        uptime = (
            int(time.monotonic() - self.server.start_time)
            if hasattr(self.server, "start_time")
            else 0
        )
        self._send_json(
            200,
            {
                "ok": True,
                "server": "deepseek-bridge",
                "uptime_seconds": uptime,
            },
        )

    def _handle_api_version(self) -> None:
        self._request_id = _generate_request_id()
        self._send_json(200, {"version": __version__})

    def _handle_api_tags(self) -> None:
        self._request_id = _generate_request_id()
        model_ids = list(
            dict.fromkeys(
                [
                    self.config.upstream_model,
                    "deepseek-v4-pro",
                    "deepseek-v4-flash",
                ]
            )
        )
        models = []
        for model_id in model_ids:
            models.append(
                {
                    "name": model_id,
                    "model": model_id,
                    "modified_at": OLLAMA_MODIFIED_AT,
                    "size": OLLAMA_MODEL_SIZE,
                    "digest": f"sha256:{hashlib.sha256(model_id.encode()).hexdigest()}",
                    "details": {
                        "format": OLLAMA_FORMAT,
                        "family": "deepseek" if "deepseek" in model_id else "custom",
                        "families": (
                            ["deepseek"] if "deepseek" in model_id else ["custom"]
                        ),
                        "parameter_size": OLLAMA_PARAMETER_SIZE,
                        "quantization_level": OLLAMA_QUANTIZATION_LEVEL,
                    },
                }
            )
        self._send_json(200, {"models": models})

    def _handle_api_show(self) -> None:
        self._request_id = _generate_request_id()
        try:
            payload = self._read_json_body()
        except (ValueError, RequestBodyTooLargeError):
            self._send_json(400, {"error": "invalid request"})
            return
        model_name = str(payload.get("model") or self.config.upstream_model)
        is_deepseek = "deepseek" in model_name
        architecture = "deepseek" if is_deepseek else "custom"
        response = {
            "modelfile": f"# Modelfile for {model_name}\nFROM {model_name}\n",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": OLLAMA_FORMAT,
                "family": architecture,
                "families": [architecture],
                "parameter_size": OLLAMA_PARAMETER_SIZE,
                "quantization_level": OLLAMA_QUANTIZATION_LEVEL,
            },
            "model_info": {
                f"{architecture}.context_length": OLLAMA_CONTEXT_LENGTH,
                f"{architecture}.embedding_length": OLLAMA_EMBEDDING_LENGTH,
            },
            "capabilities": {
                "supports": {
                    "tool_calls": True,
                    "vision": False,
                },
                "limits": {
                    "max_prompt_tokens": OLLAMA_CONTEXT_LENGTH,
                    "max_output_tokens": OLLAMA_MAX_OUTPUT_TOKENS,
                },
            },
            "modified_at": OLLAMA_MODIFIED_AT,
        }
        self._send_json(200, response)
