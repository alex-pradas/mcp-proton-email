"""pass-cli binary discovery: explicit override → PATH lookup → well-known
locations → actionable error. GUI-launched MCP clients strip PATH, so the
server must find pass-cli without relying on the caller's shell setup."""

import subprocess

import pytest

import mcp_proton_email.secrets as secrets_module
from mcp_proton_email.secrets import PassError, SecretProvider, resolve_pass_cli


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("PROTONMCP_PASS_CLI", raising=False)


def test_env_override_wins(tmp_path, monkeypatch):
    override = tmp_path / "custom-pass-cli"
    override.write_text("#!/bin/sh\n")
    override.chmod(0o755)
    monkeypatch.setenv("PROTONMCP_PASS_CLI", str(override))
    # even if PATH would find another one, the override wins
    monkeypatch.setattr(secrets_module.shutil, "which", lambda _: "/somewhere/else/pass-cli")
    assert resolve_pass_cli() == str(override)


def test_env_override_missing_is_error(monkeypatch):
    monkeypatch.setenv("PROTONMCP_PASS_CLI", "/nonexistent/pass-cli")
    with pytest.raises(PassError, match="PROTONMCP_PASS_CLI"):
        resolve_pass_cli()


def test_path_lookup_second(monkeypatch):
    monkeypatch.setattr(secrets_module.shutil, "which", lambda _: "/opt/somewhere/pass-cli")
    assert resolve_pass_cli() == "/opt/somewhere/pass-cli"


def test_well_known_fallback_third(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets_module.shutil, "which", lambda _: None)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    binary = fake_bin / "pass-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(secrets_module, "WELL_KNOWN_DIRS", (fake_bin,))
    assert resolve_pass_cli() == str(binary)


def test_not_found_error_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(secrets_module, "WELL_KNOWN_DIRS", (tmp_path / "empty",))
    with pytest.raises(PassError) as exc:
        resolve_pass_cli()
    message = str(exc.value)
    assert "brew install protonpass/tap/pass-cli" in message
    assert "PROTONMCP_PASS_CLI" in message


def test_subprocess_inherits_environment(monkeypatch, tmp_path):
    """The subprocess must see the user's real environment (locale, HOME,
    custom vars) plus the audit reason — not a hardcoded minimal PATH."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(cmd=cmd, env=kwargs["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout="hunter2\n", stderr="")

    binary = tmp_path / "pass-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PROTONMCP_PASS_CLI", str(binary))
    monkeypatch.setenv("SOME_USER_VAR", "user-value")
    monkeypatch.setattr(secrets_module.subprocess, "run", fake_run)

    provider = SecretProvider("Agent", "proton-bridge")
    assert provider.get_password() == "hunter2"
    assert captured["cmd"][0] == str(binary), "must invoke the resolved binary path"
    assert captured["env"]["SOME_USER_VAR"] == "user-value", "environment must be inherited"
    assert "PROTON_PASS_AGENT_REASON" in captured["env"]


def test_resolution_cached_on_provider(monkeypatch, tmp_path):
    calls = []
    binary = tmp_path / "pass-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    def fake_resolve():
        calls.append(1)
        return str(binary)

    monkeypatch.setattr(secrets_module, "resolve_pass_cli", fake_resolve)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="pw\n", stderr="")

    monkeypatch.setattr(secrets_module.subprocess, "run", fake_run)
    provider = SecretProvider("Agent", "proton-bridge")
    provider.get_password()
    provider.forget()
    provider.get_password()
    assert len(calls) == 1, "binary resolution should happen once per provider"
