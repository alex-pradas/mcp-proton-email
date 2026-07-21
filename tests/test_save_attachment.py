"""A9 through the real server: crafted attachment filenames cannot escape the
allow-root, existing files are never overwritten, every save is audited."""

import asyncio
import json
from email.message import EmailMessage

import pytest
from fastmcp import Client

import mcp_proton_email.server as server_module
import mcp_proton_email.tools_write as tools_write
from mcp_proton_email.server import build_server

from test_policy import make_config


def make_mail_with_attachment(filename: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "attacker@evil.example"
    msg["Subject"] = "receipt"
    msg.set_content("see attached")
    msg.add_attachment(b"%PDF-1.4 fake receipt", maintype="application",
                       subtype="pdf", filename=filename)
    return msg


@pytest.fixture
def env(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(server_module, "AUDIT_DIR", tmp_path / "audit")
    config = make_config(tmp_path, allow_send=False)
    object.__setattr__(config, "attachment_dir", downloads)
    return config, downloads, tmp_path / "audit"


def call_save(config, evil_name: str, monkeypatch, filename_override: str | None = None):
    monkeypatch.setattr(
        tools_write, "fetch_full", lambda client, folder, uid: make_mail_with_attachment(evil_name)
    )
    monkeypatch.setattr(
        "mcp_proton_email.state.AppState.connection",
        lambda self, account=None: type("C", (), {"run": lambda self2, fn: fn(None)})(),
    )
    args = {"folder": "INBOX", "uid": 1, "attachment_index": 0}
    if filename_override:
        args["filename"] = filename_override

    async def run():
        async with Client(build_server(config)) as client:
            return await client.call_tool("save_attachment", args, raise_on_error=False)

    return asyncio.run(run())


@pytest.mark.parametrize("evil", ["../../../etc/cron.d/pwn", "/etc/pwn.pdf", "..\\..\\pwn.exe"])
def test_traversal_names_stay_inside_root(env, monkeypatch, evil):
    config, downloads, _ = env
    result = call_save(config, evil, monkeypatch)
    assert not result.is_error, result.content[0].text
    saved = result.data["saved_path"]
    assert saved.startswith(str(downloads)), f"escaped the allow-root: {saved}"
    files = list(downloads.iterdir())
    assert len(files) == 1 and files[0].read_bytes().startswith(b"%PDF")


def test_never_overwrites(env, monkeypatch):
    config, downloads, _ = env
    (downloads / "receipt.pdf").write_bytes(b"precious existing file")
    result = call_save(config, "receipt.pdf", monkeypatch)
    assert not result.is_error
    assert (downloads / "receipt.pdf").read_bytes() == b"precious existing file"
    assert result.data["saved_path"].endswith("receipt-1.pdf")


def test_symlink_in_root_refused(env, monkeypatch):
    config, downloads, _ = env
    outside = downloads.parent / "outside"
    outside.mkdir()
    (downloads / "receipt.pdf").symlink_to(outside / "victim.pdf")
    result = call_save(config, "receipt.pdf", monkeypatch)
    # dedup may sidestep the symlink or refuse — either way nothing outside root
    assert not (outside / "victim.pdf").exists()


def test_save_is_audited(env, monkeypatch):
    config, downloads, audit_dir = env
    result = call_save(config, "receipt.pdf", monkeypatch)
    assert not result.is_error
    entries = [json.loads(line) for line in (audit_dir / "audit.log").read_text().splitlines()]
    save_entries = [e for e in entries if e["tool"] == "save_attachment"]
    assert save_entries and save_entries[0]["saved_path"] == result.data["saved_path"]


def test_read_only_blocks_save(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "AUDIT_DIR", tmp_path / "audit")
    config = make_config(tmp_path, read_only=True)

    async def run():
        async with Client(build_server(config)) as client:
            return await client.call_tool(
                "save_attachment", {"folder": "INBOX", "uid": 1, "attachment_index": 0},
                raise_on_error=False,
            )

    result = asyncio.run(run())
    assert result.is_error, "save_attachment must not exist under READ_ONLY (A11)"
