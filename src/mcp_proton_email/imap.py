"""IMAP connection management: one imapclient connection per account, behind a lock.

Structured imapclient API only — no raw command strings (spec 4). TLS verification
is relaxed only for the loopback Bridge with its self-signed certificate.
"""

import ssl
import threading
from collections.abc import Callable
from datetime import date, datetime

from imapclient import IMAPClient
from imapclient.imap4 import IMAP4WithTimeout

from .config import Config
from .secrets import SecretProvider


def _open_compat(self, host: str = "", port: int = 143, timeout: float | None = None) -> None:
    # imapclient 3.1.0 assigns IMAP4.file, which Python 3.14 made a read-only
    # property backed by _file. (Its starttls() has the same bug; we call the
    # stdlib starttls instead, which handles _file correctly.)
    self.host = host
    self.port = port
    self.sock = self._create_socket(timeout)
    file = self.sock.makefile("rb")
    try:
        self.file = file  # Python <= 3.13
    except AttributeError:
        self._file = file  # Python >= 3.14


IMAP4WithTimeout.open = _open_compat


def bridge_ssl_context() -> ssl.SSLContext:
    # Loopback-only Bridge presents a self-signed cert; verification is
    # intentionally relaxed for it (spec 4) and this fact is documented.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class MailConnection:
    def __init__(self, config: Config, secrets: SecretProvider, username: str) -> None:
        self._config = config
        self._secrets = secrets
        self.username = username
        self._client: IMAPClient | None = None
        self._lock = threading.RLock()

    def _connect(self) -> IMAPClient:
        client = IMAPClient(
            self._config.imap_host,
            port=self._config.imap_port,
            ssl=False,
            timeout=60,
        )
        # imapclient 3.1.0's starttls() assigns IMAP4.file, which is a read-only
        # property on Python 3.14 — stdlib imaplib's own starttls works, use it.
        client._imap.starttls(ssl_context=bridge_ssl_context())
        client.login(self.username, self._secrets.get_password())
        return client

    def run[T](self, fn: Callable[[IMAPClient], T]) -> T:
        """Run fn against a live connection, reconnecting once on failure."""
        with self._lock:
            if self._client is None:
                self._client = self._connect()
            try:
                self._client.noop()
            except Exception:
                try:
                    self._client.shutdown()
                except Exception:
                    pass
                self._client = self._connect()
            return fn(self._client)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.logout()
                except Exception:
                    pass
                self._client = None


def imap_search(client, criteria: list[object]) -> list[int]:
    """Run an IMAP SEARCH, using CHARSET UTF-8 when any term is non-ASCII.

    imapclient encodes criteria as ASCII by default, which raises on accented
    names or punctuation like em-dashes. Bridge accepts CHARSET UTF-8.
    """
    needs_utf8 = any(isinstance(c, str) and not c.isascii() for c in criteria)
    return client.search(criteria, charset="UTF-8" if needs_utf8 else None)


def build_search_criteria(
    from_addr: str | None = None,
    to_addr: str | None = None,
    subject: str | None = None,
    since: str | None = None,
    before: str | None = None,
    unseen_only: bool = False,
    flagged_only: bool = False,
    body_text: str | None = None,
) -> list[object]:
    """Structured IMAP SEARCH criteria (imapclient handles quoting/encoding)."""
    criteria: list[object] = []
    if from_addr:
        criteria += ["FROM", from_addr]
    if to_addr:
        criteria += ["TO", to_addr]
    if subject:
        criteria += ["SUBJECT", subject]
    if since:
        criteria += ["SINCE", _parse_date(since)]
    if before:
        criteria += ["BEFORE", _parse_date(before)]
    if unseen_only:
        criteria.append("UNSEEN")
    if flagged_only:
        criteria.append("FLAGGED")
    if body_text:
        criteria += ["TEXT", body_text]
    return criteria or ["ALL"]


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"dates must be YYYY-MM-DD, got {value!r}") from exc
