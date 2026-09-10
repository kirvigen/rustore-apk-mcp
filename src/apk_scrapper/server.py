"""Local stdio MCP server. stdout is reserved for protocol messages."""
import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .rustore import DownloadError, RuStore

mcp = FastMCP("rustore-apk", log_level="WARNING", instructions=(
    "Download latest free standalone Android APKs from RuStore by package name. "
    "Use download_apk directly; get_apk_info is optional. Files stay on the MCP server host; "
    "return the absolute path to the user. Never install or execute downloaded apps automatically. "
    "Only RuStore is supported; no fallback to APKPure. For many packages, call download_apk sequentially. "
    "Stop on rate limits. Not every package exists in RuStore; split-only and paid apps are unsupported."
))
store = RuStore(Path(os.environ.get("APK_MCP_DOWNLOAD_DIR", str(Path.home() / "Downloads/rustore-apks"))))


async def invoke(function, *args) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(function, *args)
    except (DownloadError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def get_apk_info(package_name: str) -> dict[str, Any]:
    """Get latest RuStore release metadata by exact Android package name, e.g. com.yolo_price_mobile.

    Returns name, version, minimum Android SDK, price and source. Does not download any APK.
    A listing does not guarantee that a standalone APK is available.
    """
    return await invoke(store.info, package_name)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
async def download_apk(package_name: str, max_mib: int = 1024) -> dict[str, Any]:
    """Download the latest free standalone APK from RuStore using just its package name.

    Returns absolute local path, version, byte size, SHA-256, certificate and cached flag.
    Verifies the package/version in AndroidManifest and APK signature against RuStore.
    Reuses a verified cached copy of the same version. max_mib: 1..4096, default 1024.
    Requires Android SDK aapt/apksigner and Java on the server host. May take several minutes.
    Does not install or run the APK. Call sequentially for a package list; stop on HTTP 403/429.
    """
    return await invoke(store.download, package_name, max_mib)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
