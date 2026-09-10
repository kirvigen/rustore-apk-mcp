import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_tools_and_validation():
    params = StdioServerParameters(command=sys.executable, args=["-m", "apk_scrapper.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {"get_apk_info", "download_apk"}
            assert tools["get_apk_info"].annotations.readOnlyHint is True
            assert tools["download_apk"].annotations.readOnlyHint is False
            assert tools["download_apk"].inputSchema["required"] == ["package_name"]
            assert tools["download_apk"].outputSchema is not None
            result = await session.call_tool("download_apk", {"package_name": "../bad"})
            assert result.isError
            assert "package name" in result.content[0].text
