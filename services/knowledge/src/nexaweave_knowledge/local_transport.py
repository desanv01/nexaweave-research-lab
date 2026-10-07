"""Knowledge-only literal-loopback admission and HTTP request enforcement."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


class LocalPolicyViolation(ValueError):
    """Fixed messages deliberately exclude endpoint and credential payloads."""


@dataclass(frozen=True)
class LocalEndpoint:
    scheme: str
    host: str
    port: int
    path: str


def local_endpoint(value: str, *, bolt: bool = False, request: bool = False) -> LocalEndpoint:
    error = "invalid local knowledge endpoint"
    if not isinstance(value, str) or re.search(r"[\s\\%\x00-\x1f\x7f]", value):
        raise LocalPolicyViolation(error)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        # HTTPX canonicalizes explicit :80/:443 out of request URL objects.
        # Admission still requires an explicit port; transport compares the
        # effective HTTP origin after that normalization.
        normalized_default = request and not bolt and port is None
        if normalized_default:
            port = {"http": 80, "https": 443}.get(parsed.scheme)
        expected_authority = f"[{host}]:{port}" if host == "::1" else f"{host}:{port}"
        if normalized_default:
            expected_authority = f"[{host}]" if host == "::1" else host
        if (parsed.scheme not in ({"bolt"} if bolt else {"http", "https"})
                or host not in {"127.0.0.1", "::1"} or port is None
                or not 1 <= port <= 65535 or parsed.netloc != expected_authority
                or parsed.query or parsed.fragment or "?" in value or "#" in value):
            raise ValueError
        path = parsed.path
        if bolt:
            if path:
                raise ValueError
        elif path and (not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*/?", path)):
            raise ValueError
        return LocalEndpoint(parsed.scheme, host, port, path.rstrip("/"))
    except ValueError:
        raise LocalPolicyViolation(error) from None


class LocalTransport(httpx.AsyncBaseTransport):
    """Own a real HTTP transport; deny escape before DNS or connect."""

    def __init__(self, base_url: str):
        self.endpoint = local_endpoint(base_url)
        self._transport = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        self._closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # raw_path retains encoded dot/slash ambiguity discarded by decoded paths.
        raw = request.url.raw_path
        if b"%" in raw or b"\\" in raw:
            raise LocalPolicyViolation("local knowledge request denied")
        try:
            target = local_endpoint(str(request.url), request=True)
        except LocalPolicyViolation:
            raise LocalPolicyViolation("local knowledge request denied") from None
        base = self.endpoint
        if ((target.scheme, target.host, target.port) != (base.scheme, base.host, base.port)
                or not (target.path == base.path or target.path.startswith(base.path + "/"))):
            raise LocalPolicyViolation("local knowledge request denied")
        response = await self._transport.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise LocalPolicyViolation("local knowledge redirect denied")
        return response

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._transport.aclose()
