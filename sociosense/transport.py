"""Minimal HTTP transport with retries, jitter and Retry-After support.

Standard library only, so the package has no install step.  Swapping in
``httpx``/``requests`` later only requires implementing ``get_json``.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


class TransportError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class HttpTransport:
    """GET JSON with bounded exponential backoff."""

    def __init__(
        self,
        timeout_s: float = 10.0,
        max_retries: int = 3,
        backoff_s: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._sleep = sleeper
        self._rand = rand

    def get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        query = _encode_params(params or {})
        full_url = f"{url}?{query}" if query else url
        last: TransportError | None = None

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(full_url, headers=dict(headers or {}), method="GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    body = response.read().decode("utf-8")
                if not body.strip():
                    return {}
                parsed = json.loads(body)
                if not isinstance(parsed, dict):
                    raise TransportError("expected a JSON object", body=body)
                return parsed
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                err = TransportError(f"HTTP {exc.code} for {url}", status=exc.code, body=body)
                if exc.code not in RETRYABLE_STATUS or attempt == self.max_retries:
                    raise err from None
                last = err
                retry_after = _retry_after(exc.headers)
                self._sleep(retry_after if retry_after is not None else self._delay(attempt))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                err = TransportError(f"request failed for {url}: {exc}")
                if attempt == self.max_retries:
                    raise err from exc
                last = err
                self._sleep(self._delay(attempt))

        raise last or TransportError("request failed")

    def _delay(self, attempt: int) -> float:
        base = self.backoff_s * (2 ** attempt)
        return base + self._rand() * self.backoff_s


def _retry_after(headers: Mapping[str, str] | None) -> float | None:
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _encode_params(params: Mapping[str, Any]) -> str:
    flat: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            flat.append((key, "true" if value else "false"))
        elif isinstance(value, (list, tuple)):
            flat.append((key, ",".join(str(item) for item in value)))
        else:
            flat.append((key, str(value)))
    return urllib.parse.urlencode(flat)


@dataclass
class RecordedCall:
    url: str
    params: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class RecordingTransport:
    """Wraps a transport and records calls (used by tests and traces)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[RecordedCall] = []

    def get_json(self, url: str, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None):
        self.calls.append(RecordedCall(url=url, params=dict(params or {}), headers=dict(headers or {})))
        return self.inner.get_json(url, params, headers)
