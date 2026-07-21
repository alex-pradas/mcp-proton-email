"""Entry point: `python -m mcp_proton_email` runs the stdio MCP server."""

from .server import build_server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
