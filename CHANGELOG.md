# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-21

Initial release. A safe-by-default MCP server for Proton Mail via Proton
Bridge's localhost IMAP/SMTP, running locally on macOS.

### Added

- **Read tools**: `search_messages`, `get_message` (plain-text, HTML stripped),
  `get_thread`, `list_folders`, `list_message_attachments`,
  `get_attachment_text` (in-memory extraction from text/HTML/PDF/ics).
- **Draft tools**: `create_draft`, `update_draft`, `create_reply_draft`,
  `create_forward_draft` — stored in Proton's Drafts folder via IMAP APPEND.
- **Organize tools** (reversible, audited): `move_message`, `archive_message`,
  `move_to_trash`, `add_label`/`remove_label`, `mark_read`/`mark_unread`,
  `star_message`/`unstar_message`, `create_folder`, `create_label`.
- **Attachment save**: `save_attachment` writes only inside an allowlisted
  directory with path-traversal protection.
- **Send tools** (off until `PROTONMCP_ALLOW_SEND=true`): `send_email`, `reply`,
  `reply_all`, `forward` — each gated by human approval via MCP elicitation.
- **Diagnostics**: `connection_status`, `runtime_status`, `get_audit_log`.
- Bridge password read from Proton Pass via `pass-cli` (binary auto-discovered);
  never stored on disk.
- Append-only audit log at `~/.mcp-proton-email/audit.log` (no message bodies).

### Security

- **Host-aware TLS**: certificate verification is disabled only for the
  self-signed loopback Bridge; non-loopback hosts are verified (`CERT_REQUIRED`
  + `check_hostname`), with `PROTONMCP_TLS_CA_FILE` to pin a self-signed remote
  Bridge and a warning when the non-loopback escape hatch is used.
- **PDF extraction is bomb-hardened**: runs in a killable subprocess with a
  wall-clock timeout, active RSS polling with a hard memory cap, a concurrency
  cap, and fd-level stdout isolation — a malicious attachment cannot exhaust the
  server.
- **Exact-value secret scrubbing**: the Bridge password is removed from any
  error/log output regardless of shape.
- **No permanent-delete tool**; loopback-only enforcement; header-injection and
  path-traversal validated; email content wrapped as untrusted; reading never
  marks mail as read.

[Unreleased]: https://github.com/alex-pradas/mcp-proton-email/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alex-pradas/mcp-proton-email/releases/tag/v0.1.0
