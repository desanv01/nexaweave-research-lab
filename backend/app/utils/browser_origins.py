"""Strict, exact browser Origin configuration for the Flask API."""

import ipaddress
import re
from typing import Iterable


DEFAULT_ALLOWED_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
_DNS_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\Z")
_IPV6_AUTHORITY = re.compile(r"\[([^\]]+)\](?::([0-9]+))?\Z")


def _invalid() -> ValueError:
    # Never include a raw configuration value in startup errors.
    return ValueError("Invalid allowed browser origin configuration")


def _validate_port(value: str | None) -> None:
    if value is None:
        return
    if (not value or len(value) > 5 or not value.isascii() or not value.isdecimal()
            or (len(value) > 1 and value.startswith("0"))):
        raise _invalid()
    port = int(value)
    if not 1 <= port <= 65535:
        raise _invalid()


def validate_origin(value: str) -> str:
    """Accept only an exact HTTP(S) scheme, host and optional port."""
    if (not isinstance(value, str) or not value or not value.isascii()
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
        raise _invalid()
    if value.startswith("http://"):
        authority = value[len("http://"):]
    elif value.startswith("https://"):
        authority = value[len("https://"):]
    else:
        raise _invalid()
    if not authority or "/" in authority or any(char in authority for char in "?#@,\\*^$(){}|"):
        raise _invalid()
    if not authority.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in authority):
        raise _invalid()

    if authority.startswith("["):
        matched = _IPV6_AUTHORITY.fullmatch(authority)
        if not matched:
            raise _invalid()
        if "%" in matched.group(1):
            raise _invalid()
        try:
            ipaddress.IPv6Address(matched.group(1))
        except ValueError:
            raise _invalid() from None
        _validate_port(matched.group(2))
    else:
        if authority.count(":") > 1:
            raise _invalid()
        host, delimiter, port = authority.partition(":")
        if not _DNS_HOST.fullmatch(host):
            raise _invalid()
        if delimiter:
            _validate_port(port)
        if host.replace(".", "").isdigit():
            try:
                ipaddress.IPv4Address(host)
            except ValueError:
                raise _invalid() from None
    return value


def parse_allowed_origins(configured: str | Iterable[str]) -> tuple[str, ...]:
    """Parse a CSV env value or a config-class iterable without repair."""
    if isinstance(configured, str):
        if configured == "":
            return ()
        values = configured.split(",")
    elif isinstance(configured, (tuple, list, set, frozenset)):
        values = list(configured)
    else:
        raise _invalid()
    if any(not isinstance(value, str) for value in values):
        raise _invalid()
    return tuple(dict.fromkeys(validate_origin(value) for value in values))
