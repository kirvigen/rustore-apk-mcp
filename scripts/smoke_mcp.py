"""Opt-in live test: uv run python scripts/smoke_mcp.py [package_name]."""
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    package = sys.argv[1] if len(sys.argv) > 1 else "com.yolo_price_mobile"
    params = StdioServerParameters(command=sys.executable, args=["-m", "apk_scrapper.server"],
        env={**os.environ, "APK_MCP_DOWNLOAD_DIR": str(Path(__file__).resolve().parents[1] / "downloads/mcp")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=600)) as session:
            await session.initialize()
            print("tools:", [t.name for t in (await session.list_tools()).tools], flush=True)
            for tool in ("get_apk_info", "download_apk", "download_apk"):
                result = await session.call_tool(tool, {"package_name": package})
                if result.isError:
                    raise RuntimeError(result.content)
                assert result.structuredContent is not None, result.content
                assert result.structuredContent["package_name"] == package
                print(json.dumps(result.structuredContent, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
