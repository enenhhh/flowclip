from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable


_ROUTE_PROBES = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("10.255.255.255", 9),
)


def _valid_local_ipv4(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.version != 4 or address.is_loopback or address.is_unspecified:
        return None
    return str(address)


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    hosts = {socket.gethostname(), socket.getfqdn()}
    for host in hosts:
        try:
            for result in socket.getaddrinfo(host, None, socket.AF_INET):
                address = _valid_local_ipv4(result[4][0])
                if address is not None:
                    addresses.add(address)
        except OSError:
            pass
    for target in _ROUTE_PROBES:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(target)
                address = _valid_local_ipv4(probe.getsockname()[0])
                if address is not None:
                    addresses.add(address)
        except OSError:
            pass
    return sorted(addresses, key=lambda value: int(ipaddress.ip_address(value)))


def server_ipv4_addresses(
    listen_host: str,
    local_addresses: Iterable[str] | None = None,
) -> list[str]:
    host = listen_host.strip()
    if host in {"", "0.0.0.0"}:
        return list(local_ipv4_addresses() if local_addresses is None else local_addresses)
    if host.lower() == "localhost":
        return ["127.0.0.1"]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            resolved = {
                result[4][0]
                for result in socket.getaddrinfo(host, None, socket.AF_INET)
            }
        except OSError:
            return []
        return sorted(resolved, key=lambda value: int(ipaddress.ip_address(value)))
    return [str(address)] if address.version == 4 else []
