"""Live smoke test for the write path — mutations ONLY on artifacts this
script creates (a draft), which ends up in Trash. Real mail is never touched.

Flow: create draft -> update it -> star/unstar -> label todo -> remove label
-> move draft to Trash -> verify audit rows.

Run: PROTONMCP_USERNAMES=you@example.com uv run python scripts/live_smoke_write.py
"""

import asyncio

from fastmcp import Client

from mcp_proton_email.config import load_config
from mcp_proton_email.server import build_server


async def main() -> None:
    self_address = load_config().primary_username
    async with Client(build_server()) as client:
        async def call(tool: str, args: dict) -> object:
            result = await client.call_tool(tool, args)
            print(f"  {tool}: {result.data}")
            return result.data

        print("1) create_draft (safe artifact)")
        draft = await call("create_draft", {
            "to": [self_address],
            "subject": "[mcp-smoke] draft lifecycle test",
            "body": "Created by live_smoke_write.py — will end in Trash.",
        })
        folder, uid = draft["folder"], draft["uid"]

        print("2) update_draft (subject change, body carries over)")
        draft = await call("update_draft", {"uid": uid, "subject": "[mcp-smoke] updated"})
        uid = draft["uid"]

        print("3) flags on the draft")
        await call("star_message", {"folder": folder, "uid": uid})
        await call("unstar_message", {"folder": folder, "uid": uid})

        print("4) label add/remove (uses the first existing label, if any)")
        folders = (await client.call_tool("list_folders", {})).data
        labels = [f.removeprefix("Labels/") for f in folders
                  if f.startswith("Labels/")]
        if labels:
            await call("add_label", {"folder": folder, "uid": uid, "label": labels[0]})
            await call("remove_label", {"folder": folder, "uid": uid, "label": labels[0]})
        else:
            print("  (no labels exist in this account — skipped)")

        print("5) move draft to Trash (reversible; purge in the Proton UI if desired)")
        await call("move_to_trash", {"folder": folder, "uid": uid})

        print("6) audit trail for this run")
        entries = await call("get_audit_log", {"limit": 10})
        tools_seen = [e["tool"] for e in entries]
        expected = ["create_draft", "update_draft", "move_to_trash"]
        if labels:
            expected.append("add_label")
        for tool in expected:
            assert tool in tools_seen, f"missing audit row for {tool}"

    print("\nWRITE SMOKE: all mutations executed on self-created draft only, all audited")


if __name__ == "__main__":
    asyncio.run(main())
