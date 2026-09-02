"""Outbound request policy shared by every worker egress path.

Anything the worker fetches from the public internet passes through here:
https only, no embedded credentials, and every resolved address checked to be
public at request time so a DNS answer cannot point at link-local or private
space after validation.
"""

import ipaddress
import socket
from urllib.parse import urlsplit


class EgressBlocked(Exception):
    """Carries a short, non-sensitive reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolved_addresses(hostname: str, port: int) -> list[str]:
    try:
        entries = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as error:
        raise EgressBlocked("destination_unresolvable") from error
    return [str(entry[4][0]) for entry in entries]


def assert_public_address(address: str) -> None:
    parsed = ipaddress.ip_address(address)
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    ):
        raise EgressBlocked("destination_not_public")


def assert_public_https_target(url: str) -> str:
    """Validate a target and return its hostname."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise EgressBlocked("destination_scheme_rejected")
    if parsed.username or parsed.password:
        raise EgressBlocked("destination_credentials_rejected")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise EgressBlocked("destination_not_public")
    for address in resolved_addresses(hostname, parsed.port or 443):
        assert_public_address(address)
    return hostname
