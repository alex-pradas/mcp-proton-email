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
    secrets: SecretProvider
    connections: dict[str, MailConnection] = field(default_factory=dict)

    def connection(self, account: str | None = None) -> MailConnection:
        username = (account or self.config.primary_username).strip()
        if username not in self.config.usernames:
            raise ToolError(
                f"unknown account {username!r}; configured accounts: {', '.join(self.config.usernames)}"
            )
        if username not in self.connections:
            self.connections[username] = MailConnection(self.config, self.secrets, username)
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

    def validate_from(self, from_addr: str | None) -> str:
        sender = (from_addr or self.config.send_from[0]).strip().lower()
        if sender not in self.config.send_from:
            raise ToolError(
                f"From address {sender!r} is not in the owned-address allowlist "
                f"({', '.join(self.config.send_from)}) — refused (spec 6.3)."
            )
        return sender
