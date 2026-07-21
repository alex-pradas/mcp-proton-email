# Installing the Proton Mail MCP Server

`mcp-proton-email` is a **local, macOS-only** MCP server that gives Claude safe
access to Proton Mail through Proton Bridge's localhost IMAP/SMTP. It reads the
Bridge password from Proton Pass on demand — nothing sensitive is stored on
disk.

## Prerequisites (all on the same Mac)

1. **macOS** and a **paid Proton plan** (Bridge requires one).
2. **Proton Mail Bridge** installed, signed in, and running:
   `brew install --cask proton-mail-bridge`
3. **Proton Pass CLI** installed and logged in:
   `brew install protonpass/tap/pass-cli && pass-cli login`
4. The **Bridge password stored in Proton Pass** — vault `Agent`, item title
   `proton-bridge`, username = your Bridge IMAP username, password = the
   Bridge-generated password:
   ```bash
   pass-cli vault create --name Agent   # if it doesn't exist
   # copy the password from Bridge -> Settings -> account -> Mailbox details, then:
   pass-cli item create login --vault-name Agent --title proton-bridge \
     --username you@example.com --password "$(pbpaste)"
   ```
5. `uv` (`brew install uv`) — it fetches the right Python automatically.

## Installation

### Option 1: uvx (recommended — no install)

```json
{
  "mcpServers": {
    "proton-mail": {
      "command": "uvx",
      "args": ["mcp-proton-email"],
      "env": { "PROTONMCP_USERNAMES": "you@example.com" }
    }
  }
}
```

For the **Claude Desktop app**, use the absolute path to `uvx` (GUI apps don't
inherit your shell PATH — find it with `which uvx`, commonly
`/opt/homebrew/bin/uvx`), then fully quit (⌘Q) and reopen the app.

### Option 2: pip / uv tool

```bash
uv tool install mcp-proton-email     # -> ~/.local/bin/mcp-proton-email
```

```json
{
  "mcpServers": {
    "proton-mail": {
      "command": "mcp-proton-email",
      "env": { "PROTONMCP_USERNAMES": "you@example.com" }
    }
  }
}
```

### Claude Code (CLI)

```bash
claude mcp add --scope user proton-mail \
  --env PROTONMCP_USERNAMES=you@example.com \
  -- uvx mcp-proton-email
```

## First run

The first time the server reads your secret, macOS shows a Keychain dialog
("pass-cli wants to use … ProtonPassCLI"). Enter your **Mac login password** and
click **Always Allow** so the server can read the Bridge password headlessly.

## Configuration (environment variables, prefix `PROTONMCP_`)

| Variable | Default | Meaning |
|---|---|---|
| `USERNAMES` | *(required)* | Bridge IMAP username(s), comma-separated |
| `ALLOW_SEND` | `false` | Enable send tools (each send still needs human approval) |
| `READ_ONLY` | `false` | Disable ALL mutation |
| `PASS_VAULT` / `PASS_ITEM` | `Agent` / `proton-bridge` | Where the Bridge password lives |
| `SEND_FROM` | primary username | Allowed From address(es) |
| `ATTACHMENT_DOWNLOAD_DIR` | `~/Downloads` | Where `save_attachment` may write |

See the [README](README.md) for the full table and security model.

## Safety posture

- **Sending is off by default**; when enabled, every send requires *your*
  approval via an MCP elicitation prompt — the model cannot approve its own
  sends, and there is no flag to bypass this.
- **No permanent-delete tool** — the most destructive action is a reversible
  move to Trash.
- **Email content is untrusted**: bodies/attachments are returned as plain text
  wrapped in an "untrusted content" marker; there is no HTTP client.
- **Reading never marks mail as read.**

## Available tools

- Read: `search_messages`, `get_message`, `get_thread`, `list_folders`,
  `list_message_attachments`, `get_attachment_text`
- Attachments: `save_attachment`
- Drafts: `create_draft`, `update_draft`, `create_reply_draft`,
  `create_forward_draft`
- Organize: `move_message`, `archive_message`, `move_to_trash`, `add_label`,
  `remove_label`, `mark_read`/`mark_unread`, `star_message`/`unstar_message`,
  `create_folder`, `create_label`
- Send (opt-in): `send_email`, `reply`, `reply_all`, `forward`
- Diagnostics: `connection_status`, `runtime_status`, `get_audit_log`
