"""Bounded outbound HTTP for provider JSON and small generated audio.

The public functions are synchronous because their callers run in FastAPI's
thread pool or an agent worker. Actual I/O remains asynchronous: cancellation
closes the connection when the total deadline expires, including slow headers
and slow response bodies. The transport creates no background request workers.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

import anyio
import httpx

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ERROR_BYTES = 16 * 1024
READ_CHUNK_BYTES = 64 * 1024


class HTTPStatusError(RuntimeError):
    """An upstream HTTP failure with a bounded, possibly truncated detail."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f'HTTP {status_code}: {detail}')


class ResponseTooLargeError(ValueError):
    pass


class RequestCancelledError(RuntimeError):
    pass


def _check_current(is_current: Callable[[], bool] | None) -> None:
    if is_current is not None and not is_current():
        raise RequestCancelledError('provider request was cancelled')


async def _read_response(response: httpx.Response, max_bytes: int, is_current) -> bytes:
    _check_current(is_current)
    encoding = response.headers.get('content-encoding', 'identity').strip().lower()
    if encoding not in {'', 'identity'}:
        raise ValueError(f'unsupported provider Content-Encoding: {encoding}')
    failed = not response.is_success
    limit = MAX_ERROR_BYTES if failed else max_bytes
    length = response.headers.get('content-length')
    if not failed and length is not None:
        try:
            declared_length = int(length)
        except ValueError:
            declared_length = -1
        if declared_length > limit:
            raise ResponseTooLargeError(f'provider response exceeds {limit} bytes')
    data = bytearray()
    truncated = False
    async for chunk in response.aiter_raw(chunk_size=min(READ_CHUNK_BYTES, limit + 1)):
        _check_current(is_current)
        remaining_bytes = limit - len(data)
        if len(chunk) > remaining_bytes:
            if not failed:
                raise ResponseTooLargeError(f'provider response exceeds {limit} bytes')
            data.extend(chunk[:remaining_bytes])
            truncated = True
            break
        data.extend(chunk)
    _check_current(is_current)
    if failed:
        detail = data.decode('utf-8', errors='replace')
        if truncated:
            detail += ' [truncated]'
        raise HTTPStatusError(response.status_code, detail)
    return bytes(data)


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    return url.scheme, url.host, url.port or (443 if url.scheme == 'https' else 80)


async def _request_bytes(
    url: str, *, deadline: float, timeout: float, method: str,
    headers: Mapping[str, str] | None, json_body: Any, max_bytes: int,
    is_current: Callable[[], bool] | None,
) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f'provider request timeout after {timeout:g}s')
    _check_current(is_current)
    # Raw reads bound allocation before decompression. Provider APIs normally
    # honor identity; fail clearly if one does not.
    request_headers = httpx.Headers(headers)
    request_headers['Accept-Encoding'] = 'identity'
    try:
        with anyio.fail_after(remaining):
            async with httpx.AsyncClient(follow_redirects=False) as client:
                request = client.build_request(
                    method, url, headers=request_headers, json=json_body,
                    timeout=httpx.Timeout(timeout),
                )
                for redirect_count in range(6):
                    response = await client.send(request, stream=True, follow_redirects=False)
                    try:
                        next_request = response.next_request
                        if next_request is None:
                            return await _read_response(response, max_bytes, is_current)
                        if redirect_count == 5:
                            raise OSError('provider HTTP transport failed: too many redirects')
                        if _origin(request.url) != _origin(next_request.url):
                            # Cookies are scoped by host, not origin/port. Without
                            # clearing the jar, HTTPX can restore the old origin's
                            # cookie on a later same-origin redirect at the target.
                            client.cookies.clear()
                            for name in ('Authorization', 'Proxy-Authorization', 'X-Api-Key', 'Api-Key', 'Cookie'):
                                next_request.headers.pop(name, None)
                        request = next_request
                        _check_current(is_current)
                    finally:
                        # Automatic redirect handling reads whole redirect bodies.
                        # Close them instead, keeping those bytes out of memory.
                        await response.aclose()
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise TimeoutError(f'provider request timeout after {timeout:g}s') from exc
    except httpx.HTTPError as exc:
        # Do not embed the request URL: it can contain credentials or query keys.
        raise OSError(f'provider HTTP transport failed: {type(exc).__name__}') from exc
    raise OSError('provider HTTP transport failed: redirect limit exhausted')


def request_bytes(
    url: str, *, timeout: float, method: str = 'GET',
    headers: Mapping[str, str] | None = None, json_body: Any = None,
    max_bytes: int = MAX_JSON_BYTES,
    is_current: Callable[[], bool] | None = None,
) -> bytes:
    """Fetch at most ``max_bytes`` with one total network deadline.

    HTTP(S) only. Redirects retain the same deadline; provider credentials are
    removed when crossing origins. OS DNS resolver shutdown remains subject
    to platform behavior; the deadline cancels connected network operations.
    """
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('provider timeout must be finite and greater than zero')
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError('provider response limit must be a positive integer')
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise ValueError('provider request requires a valid http(s) URL') from exc
    if parsed.scheme not in {'http', 'https'} or not parsed.host:
        raise ValueError('provider request requires an http(s) URL')
    deadline = time.monotonic() + timeout
    return anyio.run(partial(
        _request_bytes, url, deadline=deadline, timeout=timeout, method=method,
        headers=headers, json_body=json_body, max_bytes=max_bytes,
        is_current=is_current,
    ))


def request_json(
    url: str, *, timeout: float, method: str = 'GET',
    headers: Mapping[str, str] | None = None, json_body: Any = None,
    max_bytes: int = MAX_JSON_BYTES,
) -> Any:
    """Fetch and decode a bounded UTF-8 JSON response."""
    started = time.monotonic()
    data = request_bytes(
        url, timeout=timeout, method=method, headers=headers,
        json_body=json_body, max_bytes=max_bytes,
    )

    def reject_constant(value: str) -> None:
        raise ValueError(f'provider JSON contains an invalid numeric constant: {value}')

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('provider JSON contains a non-finite number')
        return number

    try:
        body = json.loads(data.decode('utf-8'), parse_constant=reject_constant, parse_float=finite_float)
    except RecursionError as exc:
        raise ValueError('provider JSON exceeded the supported nesting depth') from exc
    if time.monotonic() - started >= timeout:
        raise TimeoutError(f'provider request timeout after {timeout:g}s')
    return body
