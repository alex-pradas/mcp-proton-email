"""Live smoke test (read-only) against the real Bridge via the real MCP server.

Run: PROTONMCP_USERNAMES=you@example.com uv run python scripts/live_smoke_read.py
"""

import asyncio
import json

from fastmcp import Client

from mcp_proton_email.server import build_server


def show(label: str, data: object, cap: int = 700) -> None:
    text = json.dumps(data, indent=1, default=str, ensure_ascii=False)
    print(f"\n== {label} ==\n{text[:cap]}{' …' if len(text) > cap else ''}")


async def main() -> None:
    async with Client(build_server()) as client:
        tools = await client.list_tools()
        print(f"tools registered: {sorted(t.name for t in tools)}")

        status = (await client.call_tool("connection_status", {})).data
        show("connection_status", status)

        show("runtime_status", (await client.call_tool("runtime_status", {})).data)

        folders = (await client.call_tool("list_folders", {})).data
        show("list_folders", folders)

        results = (await client.call_tool(
            "search_messages", {"folder": "INBOX", "limit": 5}
        )).data
        show("search_messages INBOX limit=5", results, cap=1500)

        if results:
            newest = results[0]
            msg = (await client.call_tool(
                "get_message", {"folder": "INBOX", "uid": newest["uid"]}
            )).data
            msg["body"] = msg["body"][:300] + " …[preview truncated by smoke script]"
            show("get_message (newest)", msg, cap=1200)

            thread = (await client.call_tool(
                "get_thread", {"folder": "INBOX", "uid": newest["uid"]}
            )).data
            print(f"\n== get_thread == {len(thread)} message(s) in conversation")

            atts = (await client.call_tool(
                "list_message_attachments", {"folder": "INBOX", "uid": newest["uid"]}
            )).data
            show("list_message_attachments", atts)

        unread = (await client.call_tool(
            "search_messages", {"folder": "INBOX", "unseen_only": True}
        )).data
        print(f"\n== unread in INBOX == {len(unread)}")

    print("\nREAD SMOKE: all calls completed")


if __name__ == "__main__":
    asyncio.run(main())
