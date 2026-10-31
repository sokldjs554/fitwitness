"""Which address a visit is counted under behind a proxy: the one the proxy added, never the one the client wrote."""

import pytest

from fitwitness.api import visitor


def test_the_counted_address_is_the_one_a_trusted_proxy_added_not_one_the_client_wrote():
    count = visitor.address
    # no proxy trusted, or no header: the address the connection reports, as before
    assert count("8.8.8.8", "10.0.0.9", 0) == "10.0.0.9" and count(None, "10.0.0.9", 1) == "10.0.0.9"
    # the proxy appends the real address to what the client sent: everything to its left is the client's claim
    assert count("1.1.1.1", "10.0.0.9", 1) == "1.1.1.1"
    assert count("9.9.9.9, 1.1.1.1", "10.0.0.9", 1) == "1.1.1.1"
    assert count("9.9.9.9, 4.4.4.4, 1.1.1.1", "10.0.0.9", 1) == "1.1.1.1"
    # an infrastructure hop after it (private, loopback, link-local) is skipped; one public hop is skipped by saying so
    assert count("1.1.1.1, 10.0.0.4, 169.254.1.1, 127.0.0.1", "x", 1) == "1.1.1.1"
    assert count("9.9.9.9, 1.1.1.1", "x", 2) == "9.9.9.9" and count("8.8.8.8, 9.9.9.9, 1.1.1.1", "x", 2) == "9.9.9.9"
    # Render's shape: the visitor, then an edge address that changes per request; what the client wrote stays on the left
    assert count("1.1.1.1, 104.16.0.1", "x", 2) == count("1.1.1.1, 172.64.0.9", "x", 2) == "1.1.1.1"
    assert count("9.9.9.9, 1.1.1.1, 104.16.0.1", "x", 2) == "1.1.1.1", "a header of the client's own making does not move it"
    assert count("1.1.1.1", "10.0.0.9", 2) == "10.0.0.9", "no edge entry: the address the connection reports"
    # an address the client invented that is not public, with no real one after it, counts as the connection
    assert count("198.51.100.7", "10.0.0.9", 1) == "10.0.0.9" and count("198.51.100.7, 203.0.113.9", "10.0.0.9", 1) == "10.0.0.9"
    assert count("198.51.100.7, 1.1.1.1", "10.0.0.9", 1) == "1.1.1.1", (
        "the real address the proxy added wins over the invented one before it"
    )
    # ports, IPv6 and rubbish
    assert count("1.2.3.4:5678", "x", 1) == "1.2.3.4" and count("[2606:4700::1111]:443", "x", 1) == "2606:4700::1111"
    assert count("2606:4700::1111", "x", 1) == "2606:4700::1111" and count("not-an-address, ,", "x", 1) == "x"


def test_the_number_of_proxies_defaults_to_two_on_render_and_zero_elsewhere(monkeypatch):
    for name in ("FITWITNESS_TRUSTED_PROXY_HOPS", "RENDER"):
        monkeypatch.delenv(name, raising=False)
    assert visitor.trusted_proxy_hops() == 0
    monkeypatch.setenv("RENDER", "true")
    assert visitor.trusted_proxy_hops() == 2, "the edge in front of Render's balancer adds one more entry (see the module)"
    monkeypatch.setenv("FITWITNESS_TRUSTED_PROXY_HOPS", "0")
    assert visitor.trusted_proxy_hops() == 0, "an explicit value wins over the platform default"
    monkeypatch.setenv("FITWITNESS_TRUSTED_PROXY_HOPS", "1")
    assert visitor.trusted_proxy_hops() == 1
    for bad in ("-1", "one"):
        monkeypatch.setenv("FITWITNESS_TRUSTED_PROXY_HOPS", bad)
        with pytest.raises(ValueError):
            visitor.trusted_proxy_hops()
