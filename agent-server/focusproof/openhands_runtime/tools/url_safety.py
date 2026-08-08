from __future__ import annotations

import socket
import re
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from urllib.parse import urlsplit, urlunsplit

Address = IPv4Address | IPv6Address
Resolver = Callable[[str], tuple[Address, ...]]

_LEGACY_IPV4_RE = re.compile(
    r"^(?:0[xX][0-9A-Fa-f]+|[0-9]+)"
    r"(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+)){0,3}$"
)


@dataclass(frozen=True, slots=True)
class SafeUrl:
    normalized: str
    hostname: str
    addresses: tuple[str, ...]


class UrlPolicyError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


def resolve_host(hostname: str) -> tuple[Address, ...]:
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UrlPolicyError("dns_unavailable", "The URL hostname could not be resolved.") from exc
    addresses = tuple(dict.fromkeys(ip_address(record[4][0]) for record in records))
    if not addresses:
        raise UrlPolicyError("dns_unavailable", "The URL hostname could not be resolved.")
    return addresses


class UrlSafetyPolicy:
    def __init__(self, *, allow_http: bool, resolver: Resolver = resolve_host) -> None:
        self._allow_http = allow_http
        self._resolver = resolver

    def validate(self, value: str) -> SafeUrl:
        try:
            parsed = urlsplit(value.strip())
            port = parsed.port
        except ValueError as exc:
            raise UrlPolicyError("url_invalid", "The URL is invalid.") from exc
        scheme = parsed.scheme.lower()
        allowed_schemes = {"https", "http"} if self._allow_http else {"https"}
        if scheme not in allowed_schemes:
            raise UrlPolicyError("url_scheme_blocked", "The URL scheme is not allowed.")
        if parsed.username is not None or parsed.password is not None:
            raise UrlPolicyError("url_credentials_blocked", "URLs with credentials are not allowed.")
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if not hostname:
            raise UrlPolicyError("url_host_missing", "The URL hostname is required.")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise UrlPolicyError("url_host_blocked", "The URL hostname is blocked.")

        literal = _parse_ip_literal(hostname)
        if literal is None:
            try:
                addresses = self._resolver(hostname)
            except UrlPolicyError:
                raise
            except Exception as exc:
                raise UrlPolicyError(
                    "dns_unavailable", "The URL hostname could not be resolved."
                ) from exc
        else:
            addresses = (literal,)
        if not addresses:
            raise UrlPolicyError("dns_unavailable", "The URL hostname could not be resolved.")
        if any(_is_blocked(address) for address in addresses):
            raise UrlPolicyError("url_address_blocked", "The URL resolves to a blocked address.")

        default_port = 443 if scheme == "https" else 80
        host_for_url = f"[{hostname}]" if ":" in hostname else hostname
        netloc = host_for_url if port in (None, default_port) else f"{host_for_url}:{port}"
        normalized = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
        return SafeUrl(
            normalized=normalized,
            hostname=hostname,
            addresses=tuple(str(address) for address in addresses),
        )


def _is_blocked(address: Address) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def _parse_ip_literal(hostname: str) -> Address | None:
    try:
        return ip_address(hostname)
    except ValueError:
        if not _LEGACY_IPV4_RE.fullmatch(hostname):
            return None
    try:
        return IPv4Address(socket.inet_aton(hostname))
    except OSError as exc:
        raise UrlPolicyError("url_invalid", "The URL is invalid.") from exc
