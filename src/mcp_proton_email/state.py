"""Shared application state and server-side policy enforcement (spec 6.1/6.2).

Policy is enforced here at call time, in addition to conditional tool
registration in server.py — disabled capabilities both do not exist and refuse.
"""

from dataclasses import dataclass, field

from fastmcp.exceptions import ToolError

from .audit import AuditLog
from .config import Config
from .imap import MailConnection
from .secrets import SecretProvider


@dataclass
class AppState:
    config: Config
    audit: AuditLog
    connections: dict[str, MailConnection] = field(default_factory=dict)
    _secret_providers: dict[str, SecretProvider] = field(default_factory=dict)

    def validate_account(self, account: str | None) -> str:
        """Resolve and validate an account selector against configured usernames."""
        username = (account or self.config.primary_username).strip()
        if username not in self.config.usernames:
            raise ToolError(
                f"unknown account {username!r}; configured accounts: {', '.join(self.config.usernames)}"
            )
        return username

    def secret_for(self, account: str | None = None) -> SecretProvider:
        """Per-account secret provider — each account has its own Bridge password
        in its own Proton Pass item, and its own in-memory cache."""
        username = self.validate_account(account)
        if username not in self._secret_providers:
            self._secret_providers[username] = SecretProvider(
                self.config.pass_vault, self.config.pass_item_for(username)
            )
        return self._secret_providers[username]

    def connection(self, account: str | None = None) -> MailConnection:
        username = self.validate_account(account)
        if username not in self.connections:
            self.connections[username] = MailConnection(
                self.config, self.secret_for(username), username
            )
        return self.connections[username]

    def require_mutation(self, tool: str) -> None:
        if self.config.read_only:
            raise ToolError(
                f"{tool} refused: PROTONMCP_READ_ONLY=true disables all mutation (spec 6.1)."
            )

    def require_send(self, tool: str) -> None:
        self.require_mutation(tool)
        if not self.config.allow_send:
            raise ToolError(
                f"{tool} refused: sending is disabled until PROTONMCP_ALLOW_SEND=true (spec 6.1)."
            )

    def validate_from(self, from_addr: str | None, account: str | None = None) -> str:
        # Default From is the selected account's own address (each account can
        # only send as its own addresses — Bridge enforces this too). For
        # multi-account sending, add each address to PROTONMCP_SEND_FROM.
        default = (account or self.config.primary_username)
        sender = (from_addr or default).strip().lower()
        if sender not in self.config.send_from:
            raise ToolError(
                f"From address {sender!r} is not in the owned-address allowlist "
                f"({', '.join(self.config.send_from)}) — add it to PROTONMCP_SEND_FROM (spec 6.3)."
            )
        return sender
