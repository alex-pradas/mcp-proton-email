"""Bridge password via Proton Pass (pass-cli): lazy fetch, in-memory cache.

The secret never touches env/config/files/logs/tool output (spec 6.6).

The pass-cli binary is discovered like mature tools find their helpers
(pre-commit → git): explicit override, then PATH, then well-known install
locations. GUI-launched MCP clients strip PATH, so PATH alone is not enough.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path

AGENT_REASON = "mcp-proton-email: Bridge IMAP/SMTP login"

_SESSION_ERROR_MARKERS = ("auth", "session", "login", "unauthorized", "expired", "token")

# Homebrew (ARM + Intel), uv/pipx tools, cargo, MacPorts.
WELL_KNOWN_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path.home() / ".local" / "bin",
    Path.home() / ".cargo" / "bin",
    Path("/opt/local/bin"),
)

_NOT_FOUND_HELP = (
    "pass-cli not found. Install it with `brew install protonpass/tap/pass-cli`, "
    "or point PROTONMCP_PASS_CLI at the binary if it lives somewhere unusual."
)


class PassSessionError(Exception):
    """Raised when the pass-cli session is expired/unauthenticated."""


class PassError(Exception):
    pass


def resolve_pass_cli() -> str:
    """Locate the pass-cli binary: env override -> PATH -> well-known dirs."""
    override = os.environ.get("PROTONMCP_PASS_CLI")
    if override:
        path = Path(override).expanduser()
        if not (path.is_file() and os.access(path, os.X_OK)):
            raise PassError(
                f"PROTONMCP_PASS_CLI={override!r} is not an executable file"
            )
        return str(path)

    found = shutil.which("pass-cli")
    if found:
        return found

    for directory in WELL_KNOWN_DIRS:
        candidate = directory / "pass-cli"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise PassError(_NOT_FOUND_HELP)


class SecretProvider:
    def __init__(self, vault: str, item: str) -> None:
        self._vault = vault
        self._item = item
        self._cache: str | None = None
        self._binary: str | None = None
        self._lock = threading.Lock()

    @property
    def pass_cli_path(self) -> str:
        """Resolved binary path (for diagnostics); resolves lazily, cached."""
        with self._lock:
            return self._resolve_binary()

    def _resolve_binary(self) -> str:
        if self._binary is None:
            self._binary = resolve_pass_cli()
        return self._binary

    def get_password(self) -> str:
        with self._lock:
            if self._cache is not None:
                return self._cache
            try:
                result = self._run_pass_cli()
            except subprocess.TimeoutExpired:
                raise PassSessionError(
                    "pass-cli did not respond within 30s (hung session, a hidden "
                    "macOS Keychain dialog, or a Proton rate limit). Try again in "
                    "a few minutes; if it persists, run `pass-cli login` in your "
                    "own terminal. No restart needed."
                ) from None
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if any(marker in stderr.lower() for marker in _SESSION_ERROR_MARKERS):
                    raise PassSessionError(
                        "Proton Pass session expired or not authenticated. "
                        "Run `pass-cli login` in your own terminal, then retry — "
                        "the server does not need a restart."
                    )
                raise PassError(
                    f"pass-cli failed reading item {self._item!r} from vault {self._vault!r} "
                    f"(exit {result.returncode})"
                )
            password = result.stdout.strip()
            if not password:
                raise PassError(f"pass-cli returned an empty password field for item {self._item!r}")
            self._cache = password
            return password

    def _run_pass_cli(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                self._resolve_binary(), "item", "view",
                "--vault-name", self._vault,
                "--item-title", self._item,
                "--field", "password",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            # Inherit the user's real environment (locale, HOME, proxies, ...)
            # rather than a hardcoded minimal one; add only the audit reason.
            env={**os.environ, "PROTON_PASS_AGENT_REASON": AGENT_REASON},
        )

    def forget(self) -> None:
        with self._lock:
            self._cache = None
