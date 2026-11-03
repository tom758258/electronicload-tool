"""Small local-only Worker HTTP clients; no proxies, redirects, or retries."""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (HTTPRedirectHandler, ProxyHandler, Request,
                            build_opener)

from electronicload_tool_core.errors import LoadError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _url(url: str, path: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise LoadError("invalid_url", "Invalid local Worker URL.") from exc
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or port is None or not 1 <= port <= 65535
        or parsed.username is not None or parsed.password is not None
        or parsed.path != path or parsed.query or parsed.fragment):
        raise LoadError("invalid_url", "Only numeric loopback Worker HTTP URLs are supported.")
    return url


def request(url: str, path: str, payload: dict | None = None, *, timeout: float = 5.0) -> tuple[int, dict]:
    _url(url, path)
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    req = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={"Content-Type": "application/json"} if payload is not None else {})
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            data = json.load(exc)
        except (ValueError, UnicodeError) as decode_exc:
            raise LoadError("invalid_worker_response", "Invalid Worker response.", 3) from decode_exc
        return exc.code, data
    except (URLError, OSError, ValueError) as exc:
        raise LoadError("worker_unreachable", "Worker could not be reached.", 3,
                        exception_type=type(exc).__name__) from exc


def wait_ready(url: str, timeout_ms: int = 10000) -> dict:
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000:
        raise LoadError("argument_error", "Wait timeout must be 1 to 120000 ms.")
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            code, status = request(url, "/status", timeout=min(2.0, max(.1, deadline-time.monotonic())))
            if (code == 200 and type(status.get("schema_version")) is int
                and status["schema_version"] == 3 and status.get("worker_state") == "running"
                and status.get("fatal_error") is None and status.get("run_id")):
                return status
        except LoadError as exc:
            if exc.code not in {"worker_unreachable"}:
                raise
        if time.monotonic() >= deadline:
            raise LoadError("worker_not_ready", "Worker did not become ready.", 3)
        time.sleep(min(0.1, max(0.0, deadline-time.monotonic())))
