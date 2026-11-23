"""Minimal HTTP client for one library environment (standard library only)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .envs import Env


class ApiError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, env: Env):
        self.env = env

    def call(self, method, path, body=None, ctype=None, timeout=60):
        """-> parsed JSON (or None for an empty body). Raises ApiError. GETs retry twice on network errors."""
        data = body
        if body is not None and ctype is None:
            data, ctype = json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json"
        tries = 3 if method == "GET" else 1
        for attempt in range(tries):
            req = urllib.request.Request(self.env.url + path, data=data, method=method)
            if ctype:
                req.add_header("Content-Type", ctype)
            for k, v in self.env.headers().items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
                raise ApiError(f"HTTP {exc.code} from {method} {path} on {self.env.name}: {detail}",
                               exc.code) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt + 1 < tries:
                    time.sleep(3 * (attempt + 1))
                    continue
                reason = getattr(exc, "reason", exc)
                raise ApiError(f"network error for {method} {path} on {self.env.name}: {reason}") from None

    def get(self, path, timeout=60):
        return self.call("GET", path, timeout=timeout)

    def get_or_none(self, path):
        """GET that answers None for 404 (the thing does not exist on this environment)."""
        try:
            return self.get(path)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def post(self, path, body, timeout=60):
        return self.call("POST", path, body, timeout=timeout)

    def put(self, path, body=None, timeout=60):
        return self.call("PUT", path, body, timeout=timeout)

    def patch(self, path, body, timeout=60):
        return self.call("PATCH", path, body, timeout=timeout)

    def delete(self, path, timeout=60):
        return self.call("DELETE", path, timeout=timeout)

    def post_file(self, path, meta: dict, file: Path, ctype: str, timeout=1800):
        """multipart POST: a `metadata` JSON field and a `file` field."""
        boundary = uuid.uuid4().hex
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n"
            f"Content-Type: application/json\r\n\r\n".encode(),
            json.dumps(meta, ensure_ascii=False).encode("utf-8"), b"\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"recording{Path(file).suffix.lower()}\"\r\nContent-Type: {ctype}\r\n\r\n".encode(),
            Path(file).read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode(),
        ]
        return self.call("POST", path, b"".join(parts), f"multipart/form-data; boundary={boundary}", timeout)


def items(listing) -> list:
    """A list endpoint's records, whether it answers a list or {"items": [...]}."""
    if isinstance(listing, dict):
        return listing.get("items", [])
    return listing or []
