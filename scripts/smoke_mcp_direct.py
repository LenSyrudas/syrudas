"""Directly exercise mcp_client: connect, list tools, call one. No LLM involved."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import db
from server.mcp_client import close_all, mcp_tools


async def main() -> int:
    try:
        tools = await mcp_tools()
        names = [t.name for t in tools]
        print(f"{len(tools)} MCP tools:", names[:12])
        assert any(n.startswith("filesystem_") for n in names), "filesystem tools missing"

        lister = next(t for t in tools if t.name == "filesystem_list_directory")
        result = await lister.run({"path": "D:\\projects\\syrudas\\data"})
        print("list_directory result:\n", result[:400])
        assert "workspace" in result, "expected workspace dir in listing"
        print("\nMCP DIRECT TEST PASSED")
        return 0
    finally:
        await close_all()
        await db.close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
