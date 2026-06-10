"""apply_proxy_egress — the proxy block shared by all three adapters (P1.6)."""
from __future__ import annotations

from polynoia.adapters._utils import apply_proxy_egress


def test_system_is_a_noop_keeping_inherited_proxy():
    env = {"HTTP_PROXY": "http://host:7890", "PATH": "/bin"}
    out = apply_proxy_egress(env, "system", None)
    assert out == env
    assert out is not env  # returns a copy


def test_direct_strips_all_proxy_vars_both_cases():
    env = {
        "HTTP_PROXY": "x",
        "HTTPS_PROXY": "x",
        "ALL_PROXY": "x",
        "http_proxy": "x",
        "https_proxy": "x",
        "all_proxy": "x",
        "PATH": "/bin",
    }
    out = apply_proxy_egress(env, "direct", None)
    assert out == {"PATH": "/bin"}


def test_custom_overrides_every_proxy_var():
    out = apply_proxy_egress({"PATH": "/bin"}, "custom", "http://p:1")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert out[k] == "http://p:1"
    assert out["PATH"] == "/bin"


def test_custom_without_url_is_a_noop():
    env = {"HTTP_PROXY": "http://existing:1"}
    out = apply_proxy_egress(env, "custom", None)
    assert out == env


def test_never_mutates_input():
    env = {"HTTP_PROXY": "x"}
    apply_proxy_egress(env, "direct", None)
    assert env == {"HTTP_PROXY": "x"}  # original untouched
