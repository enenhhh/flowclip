from __future__ import annotations

import sys
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.network import server_ipv4_addresses  # noqa: E402


class ServerAddressTests(unittest.TestCase):
    def test_wildcard_uses_all_local_addresses(self) -> None:
        addresses = ["10.0.0.3", "192.168.1.20"]
        self.assertEqual(server_ipv4_addresses("0.0.0.0", addresses), addresses)

    def test_loopback_does_not_advertise_unreachable_lan_urls(self) -> None:
        self.assertEqual(
            server_ipv4_addresses("127.0.0.1", ["192.168.1.20"]),
            ["127.0.0.1"],
        )

    def test_specific_binding_only_advertises_that_address(self) -> None:
        self.assertEqual(
            server_ipv4_addresses("192.168.1.20", ["10.0.0.3", "192.168.1.20"]),
            ["192.168.1.20"],
        )

    def test_ipv6_binding_is_not_misrepresented_as_ipv4(self) -> None:
        self.assertEqual(server_ipv4_addresses("::1", ["192.168.1.20"]), [])


if __name__ == "__main__":
    unittest.main()
