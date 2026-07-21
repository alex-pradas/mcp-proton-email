"""Environment configuration (PROTONMCP_ namespace) with loopback enforcement."""

import ipaddress
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "PROTONMCP_"

AUDIT_DIR = Path.home() / ".mcp-proton-email"

MAX_RESULTS_CAP = 200
MAX_BODY_CHARS_CAP = 200_000
MAX_ATTACHMENT_CHARS_CAP = 200_000
MAX_ATTACHMENT_SOURCE_BYTES = 10 * 1024 * 1024


class ConfigError(Exception):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(ENV_PREFIX + name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def _env_int(name: str, default: int, cap: int | None = None) -> int:
    raw = _env(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise ConfigError(f"{ENV_PREFIX}{name} must be an integer, got {raw!r}") from exc
    if cap is not None:
        value = min(value, cap)
    return value


def is_loopback(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Config:
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    usernames: tuple[str, ...]
    pass_vault: str
    pass_item: str
    send_from: tuple[str, ...]
    allow_send: bool
    read_only: bool
    attachment_dir: Path
    allow_non_loopback: bool
    tls_ca_file: str | None
    max_results: int
    max_body_chars: int
    max_attachment_chars: int

    @property
    def primary_username(self) -> str:
        return self.usernames[0]


def load_config() -> Config:
    usernames_raw = _env("USERNAMES") or _env("USERNAME")
    if not usernames_raw:
        raise ConfigError(f"{ENV_PREFIX}USERNAMES is required (Bridge IMAP username)")
    usernames = tuple(u.strip() for u in usernames_raw.split(",") if u.strip())
    if not usernames:
        raise ConfigError(f"{ENV_PREFIX}USERNAMES is empty")

    # Default send identity: the primary Bridge username, which is the account's
    # email address for virtually all setups. Override via PROTONMCP_SEND_FROM.
    send_from_raw = _env("SEND_FROM", usernames[0]) or ""
    send_from = tuple(a.strip().lower() for a in send_from_raw.split(",") if a.strip())

    cfg = Config(
        imap_host=_env("IMAP_HOST", "127.0.0.1") or "127.0.0.1",
        imap_port=_env_int("IMAP_PORT", 1143),
        smtp_host=_env("SMTP_HOST", "127.0.0.1") or "127.0.0.1",
        smtp_port=_env_int("SMTP_PORT", 1025),
        usernames=usernames,
        pass_vault=_env("PASS_VAULT", "Agent") or "Agent",
        pass_item=_env("PASS_ITEM", "proton-bridge") or "proton-bridge",
        send_from=send_from,
        allow_send=_env_bool("ALLOW_SEND", False),
        read_only=_env_bool("READ_ONLY", False),
        attachment_dir=Path(_env("ATTACHMENT_DOWNLOAD_DIR", str(Path.home() / "Downloads"))).expanduser(),
        allow_non_loopback=_env_bool("ALLOW_NON_LOOPBACK", False),
        tls_ca_file=_env("TLS_CA_FILE"),
        max_results=_env_int("MAX_RESULTS", 50, cap=MAX_RESULTS_CAP),
        max_body_chars=_env_int("MAX_BODY_CHARS", 50_000, cap=MAX_BODY_CHARS_CAP),
        max_attachment_chars=_env_int("MAX_ATTACHMENT_CHARS", 20_000, cap=MAX_ATTACHMENT_CHARS_CAP),
    )

    non_loopback = [
        (label, host)
        for label, host in (("IMAP_HOST", cfg.imap_host), ("SMTP_HOST", cfg.smtp_host))
        if not is_loopback(host)
    ]
    if non_loopback and not cfg.allow_non_loopback:
        label, host = non_loopback[0]
        raise ConfigError(
            f"{ENV_PREFIX}{label}={host!r} is not a loopback address. "
            f"Refusing to start (set {ENV_PREFIX}ALLOW_NON_LOOPBACK=true to override; discouraged)."
        )
    if non_loopback:
        # Escape hatch engaged: warn loudly. TLS is still VERIFIED for these
        # hosts (see imap.bridge_ssl_context) — pin a self-signed remote Bridge
        # with PROTONMCP_TLS_CA_FILE, or connections will fail closed.
        hosts = ", ".join(f"{label}={host}" for label, host in non_loopback)
        warnings.warn(
            f"ALLOW_NON_LOOPBACK is set; connecting to non-loopback Bridge host(s): "
            f"{hosts}. TLS certificates WILL be verified"
            + (
                f" against {cfg.tls_ca_file}."
                if cfg.tls_ca_file
                else " against system CAs (set PROTONMCP_TLS_CA_FILE to pin a "
                "self-signed remote Bridge)."
            ),
            stacklevel=2,
        )
    return cfg
