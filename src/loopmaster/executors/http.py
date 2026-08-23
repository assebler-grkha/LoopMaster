"""Pure stdlib HTTP request tool executor with template resolution and OTel tracing."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .base import BaseExecutor, resolve_template_value

logger = logging.getLogger("loopmaster.executors.http")


@dataclass
class HTTPResult:
    """Structured result of an HTTP execution."""

    status_code: int
    body: Any
    headers: dict[str, str]
    success: bool
    error: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "body": self.body,
            "headers": self.headers,
            "success": self.success,
            "error": self.error,
        }


def _parse_body(raw_bytes: bytes, json_output: bool) -> Any:
    """Decode and optionally JSON parse response payload."""
    if not raw_bytes:
        return {} if json_output else ""
    text = raw_bytes.decode("utf-8", "replace")
    if json_output:
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


class HTTPExecutor(BaseExecutor):
    """Executes HTTP requests using Python standard library urllib."""

    def __init__(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_data: Any | None = None,
        data: str | bytes | None = None,
        timeout: float = 30.0,
        json_output: bool = True,
        allowed_status: list[int] | None = None,
    ) -> None:
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.json_data = json_data
        self.data = data
        self.timeout = timeout
        self.json_output = json_output
        self.allowed_status = allowed_status or [200, 201, 202, 204]

    def _prepare_request(self, ctx_data: dict[str, Any]) -> tuple[urllib.request.Request, str]:
        resolved_url = str(resolve_template_value(self.url, ctx_data))
        resolved_headers = {
            k: str(resolve_template_value(v, ctx_data)) for k, v in self.headers.items()
        }

        body_bytes: bytes | None = None
        if self.json_data is not None:
            resolved_json = resolve_template_value(self.json_data, ctx_data)
            body_bytes = json.dumps(resolved_json).encode("utf-8")
            resolved_headers.setdefault("Content-Type", "application/json")
        elif self.data is not None:
            body_bytes = self.data.encode("utf-8") if isinstance(self.data, str) else self.data

        req = urllib.request.Request(
            url=resolved_url,
            data=body_bytes,
            headers=resolved_headers,
            method=self.method,
        )
        return req, resolved_url

    def _send_request(self, req: urllib.request.Request) -> HTTPResult:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", 200)
                raw = resp.read()
                resp_headers = dict(resp.headers.items()) if hasattr(resp, "headers") else {}
                body = _parse_body(raw, self.json_output)
                success = status in self.allowed_status
                err = None if success else f"HTTP status {status} not in allowed statuses"
                return HTTPResult(
                    status_code=status, body=body, headers=resp_headers, success=success, error=err
                )
        except urllib.error.HTTPError as err:
            raw_err = err.read()
            err_headers = dict(err.headers.items()) if hasattr(err, "headers") else {}
            body = _parse_body(raw_err, self.json_output)
            success = err.code in self.allowed_status
            err_msg = None if success else f"HTTP {err.code}: {err.reason}"
            return HTTPResult(
                status_code=err.code,
                body=body,
                headers=err_headers,
                success=success,
                error=err_msg,
            )
        except Exception as exc:
            return HTTPResult(
                status_code=0,
                body=None,
                headers={},
                success=False,
                error=f"Network request failed: {exc}",
            )

    def execute(self, ctx_data: dict[str, Any]) -> HTTPResult:
        """Execute the HTTP request within a CLIENT OTel span."""
        req, resolved_url = self._prepare_request(ctx_data)

        from ..telemetry import SpanKind, SpanStatusCode, get_tracer

        tracer = get_tracer()
        with tracer.start_as_current_span(
            "tool.http",
            kind=SpanKind.CLIENT,
            attributes={"http.request.method": self.method, "url.full": resolved_url},
        ) as span:
            result = self._send_request(req)
            span.set_attribute("http.response.status_code", result.status_code)
            if not result.success:
                span.set_status(
                    SpanStatusCode.ERROR, result.error or f"Status {result.status_code}"
                )
            return result

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration for export."""
        d: dict[str, Any] = {
            "type": "http",
            "url": self.url,
            "method": self.method,
            "timeout": self.timeout,
        }
        if self.headers:
            d["headers"] = self.headers
        return d
