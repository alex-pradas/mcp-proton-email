"""TLS context is host-aware: CERT_NONE only for the loopback self-signed Bridge;
a verifying context for any non-loopback host (closes the silent-MITM gap that
ALLOW_NON_LOOPBACK would otherwise open)."""

import ssl

import pytest

from mcp_proton_email.imap import bridge_ssl_context


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_is_unverified_selfsigned(host):
    ctx = bridge_ssl_context(host)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


@pytest.mark.parametrize("host", ["192.0.2.1", "bridge.example.com", "100.115.223.108"])
def test_non_loopback_is_verified(host):
    ctx = bridge_ssl_context(host)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_non_loopback_with_ca_file_loads_it(tmp_path, monkeypatch):
    # A self-signed remote Bridge: user pins its cert via PROTONMCP_TLS_CA_FILE.
    ca = tmp_path / "bridge-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nnot-a-real-cert\n-----END CERTIFICATE-----\n")
    loaded = {}
    real_ctx = ssl.create_default_context()
    monkeypatch.setattr(
        real_ctx, "load_verify_locations",
        lambda cafile=None, **k: loaded.update(cafile=cafile),
    )
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: real_ctx)
    ctx = bridge_ssl_context("192.0.2.1", ca_file=str(ca))
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert loaded["cafile"] == str(ca)


def test_non_loopback_missing_ca_file_errors(tmp_path):
    with pytest.raises(ValueError, match="TLS_CA_FILE"):
        bridge_ssl_context("192.0.2.1", ca_file=str(tmp_path / "nope.pem"))
