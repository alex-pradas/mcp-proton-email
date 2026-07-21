--8<-- "README.md:security-model"

## Defense in depth: gated twice

Capability policy is enforced structurally, in two independent places:

1. **At registration** — disabled categories are never registered. With
   `PROTONMCP_READ_ONLY=true` no mutating tool exists; without
   `PROTONMCP_ALLOW_SEND=true` no send tool exists. A tool that isn't there
   cannot be called, listed, or tricked into running.
2. **At call time** — every mutating tool re-checks the same policy inside
   the call before doing anything, so even a registration bug could not
   bypass the configuration.

## Threat model

What this server defends against, and how:

| Threat | Mitigation |
|---|---|
| **Prompt injection** — a malicious email tells the model what to do | Bodies and attachment text are reduced to plain text (HTML never rendered) and wrapped in an `[UNTRUSTED CONTENT …]` marker. The tool surface is designed so that even a fully hijacked model has nothing catastrophic to call: no delete, no unattended send. The server contains no HTTP client, so nothing can be fetched or exfiltrated through it. |
| **Autonomous sending** — the model mails someone without you | Send tools are unregistered by default. When enabled, every send raises an MCP elicitation prompt that only *you* can approve — clients without elicitation get a refusal, with no fallback path the model could satisfy. The `From` address must be in the `PROTONMCP_SEND_FROM` allowlist. |
| **Destructive actions** — losing mail | No permanent-delete tool exists in any configuration. The most destructive mailbox operation is a reversible move to Trash; emptying it stays in the Proton apps. The one expunging operation is `update_draft`, which replaces the previous version of the draft it edits. Every mutation is audited. |
| **Malicious attachments** — PDF bombs, resource exhaustion | Attachment text extraction is in-memory only (source capped at 10 MB). PDF parsing runs in a killable subprocess with a wall-clock timeout, active RSS polling against a hard memory cap, a concurrency cap, and fd-level stdout isolation — a hostile attachment cannot exhaust the server. |
| **Credential theft** | The Bridge password is read on demand from Proton Pass via `pass-cli`, cached only in process memory, and never written to disk. It is scrubbed from any error or log output by exact value, backed by pattern-based redaction for other credential shapes. |
| **Network interception** | Loopback-only by default — the server refuses to start against a non-loopback Bridge host. TLS verification is relaxed only for the self-signed loopback Bridge (no network path to intercept); any non-loopback host gets full verification (`CERT_REQUIRED` + `check_hostname`), with `PROTONMCP_TLS_CA_FILE` to pin a self-signed remote Bridge and a startup warning whenever the escape hatch is engaged. |
| **Path traversal** — attachment filenames escaping the download dir | `save_attachment` sanitizes filenames (traversal, absolute paths, symlinks) and resolves strictly inside `PROTONMCP_ATTACHMENT_DOWNLOAD_DIR`; existing files are never overwritten. |
| **Header injection** — smuggling extra recipients or headers | All addresses and composed headers are validated before a message is built. |

## The audit log

Every mutation — drafts, organize operations, attachment saves, and sends
(including approved sends that then failed at SMTP, recorded as `error`) —
is appended to `~/.mcp-proton-email/audit.log` (directory `0700`, file
`0600`), rotated at 5 MB. Entries record the tool, outcome, account, and
what each tool composed — recipients and subject for sends and new drafts,
recipients for reply/forward drafts — intentional accountability — but
**never message bodies**. A send you decline (or one refused because the
client lacks elicitation support) performs no action and leaves no audit
row.

The model can read the log back with
[`get_audit_log`](tools.md#get_audit_log), so "what did you do to my
mailbox?" always has a checkable answer.
