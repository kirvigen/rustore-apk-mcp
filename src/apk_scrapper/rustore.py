"""RuStore download flow, validated against com.yolo_price_mobile on 2026-09-09."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import httpx

API = "https://backapi.rustore.ru/applicationData"
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


class DownloadError(Exception):
    """Actionable failure suitable for MCP tool errors."""


class RateLimited(DownloadError):
    pass


def validate_package(package: str) -> str:
    if len(package) > 255 or not PACKAGE.fullmatch(package):
        raise DownloadError("Use an Android package name, e.g. com.yolo_price_mobile, not a URL.")
    return package


def sdk_tool(name: str) -> str:
    override = os.environ.get("APK_MCP_" + name.upper())
    if override:
        if not Path(override).is_file():
            raise DownloadError(f"Configured {name} does not exist: {override}")
        return override
    found = shutil.which(name)
    if found:
        return found
    for root in filter(None, [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                              str(Path.home() / "Library/Android/sdk"), str(Path.home() / "Android/Sdk")]):
        candidates = list((Path(root) / "build-tools").glob(f"*/{name}"))
        candidates.sort(key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.parent.name)), reverse=True)
        if candidates:
            return str(candidates[0])
    raise DownloadError(f"Install Android SDK Build Tools or set APK_MCP_{name.upper()} to {name}'s path.")


def run_tool(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DownloadError(f"Cannot run {Path(command[0]).name}: {exc}") from exc
    if result.returncode:
        raise DownloadError(f"{Path(command[0]).name} failed: {(result.stderr or result.stdout)[:1000]}")
    return result.stdout


def verify_apk(path: Path, package: str, version_code: int, certificate: str,
               aapt: str, apksigner: str) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            if "AndroidManifest.xml" not in archive.namelist():
                raise DownloadError("Not a standalone APK: AndroidManifest.xml is missing.")
            # Bound decompression before CRC verification of untrusted ZIP contents.
            if sum(i.file_size for i in archive.infolist()) > 4 * 1024**3:
                raise DownloadError("Uncompressed APK exceeds 4 GiB verification limit.")
            if archive.testzip() is not None:
                raise DownloadError("APK ZIP checksum failure.")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise DownloadError("Invalid APK/ZIP response.") from exc
    badging = run_tool([aapt, "dump", "badging", str(path)])
    match = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", badging)
    if not match or match[1] != package or int(match[2]) != version_code:
        raise DownloadError("APK package or versionCode does not match the requested RuStore release.")
    signed = run_tool([apksigner, "verify", "--print-certs", str(path)])
    certificates = re.findall(r"certificate SHA-256 digest: ([a-fA-F0-9]{64})", signed)
    if certificate.lower() not in [c.lower() for c in certificates]:
        raise DownloadError("APK signing certificate does not match RuStore metadata.")
    with path.open("rb") as stream:
        sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"package_name": match[1], "version_name": match[3], "version_code": int(match[2]),
            "sha256": sha256, "certificate_sha256": certificate.lower(),
            "verification": "ZIP CRC, manifest package/version and APK signature verified; certificate matches RuStore"}


class RuStore:
    def __init__(self, directory: Path, *, transport=None, delay: float = 1):
        self.directory = directory.expanduser().resolve()
        self.transport, self.delay = transport, delay
        self._lock = threading.Lock()
        self._last_request = 0.0

    def _client(self):
        return httpx.Client(transport=self.transport, timeout=30, follow_redirects=False,
                            headers={"ruStoreVerCode": os.environ.get("RUSTORE_VERSION_CODE", "1000"),
                                     "Accept-Encoding": "identity"})

    def _request(self, client, method: str, path: str, **kwargs) -> dict:
        time.sleep(max(0, self.delay - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()
        response = client.request(method, API + path, **kwargs)
        if response.status_code in (403, 429):
            raise RateLimited(f"RuStore HTTP {response.status_code}; stopped without retries. Try later.")
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "OK" or not isinstance(data.get("body"), dict):
            raise DownloadError(f"RuStore: {str(data.get('code'))[:100]} {str(data.get('message'))[:300]}")
        return data["body"]

    def _info(self, client, package: str) -> dict:
        data = self._request(client, "GET", f"/overallInfo/{validate_package(package)}")
        if data.get("packageName") != package:
            raise DownloadError("RuStore returned a different package.")
        return {"source": "RuStore", "package_name": package, "app_id": data["appId"],
                "name": data["appName"], "version_name": data["versionName"],
                "version_code": int(data["versionCode"]), "min_sdk": data.get("minSdkVersion"),
                "updated_at": data.get("appVerUpdatedAt"), "price": data.get("price"),
                "certificate_sha256": data.get("signature"),
                "external_source": data.get("aggregatorInfo") is not None,
                "store_url": f"https://www.rustore.ru/catalog/app/{package}"}

    def info(self, package: str) -> dict:
        with self._lock, self._client() as client:
            return self._info(client, package)

    def download(self, package: str, max_mib: int = 1024) -> dict:
        validate_package(package)
        if not 1 <= max_mib <= 4096:
            raise DownloadError("max_mib must be between 1 and 4096.")
        aapt, apksigner = sdk_tool("aapt"), sdk_tool("apksigner")
        with self._lock, self._client() as client:
            info = self._info(client, package)
            if info["price"] != 0:
                raise DownloadError("Only applications explicitly marked free are supported.")
            if info["external_source"]:
                raise DownloadError("This RuStore listing uses an external source; direct download is unsupported.")
            release = self._request(client, "POST", "/v2/download-link", json={
                "appId": info["app_id"], "firstInstall": True, "screenDensity": 480,
                "sdkVersion": 36, "withoutSplits": True, "supportedAbis": ["arm64-v8a"]})
            if release.get("versionCode") != info["version_code"]:
                raise DownloadError("Release changed between requests; call again to fetch the new release.")
            certificate = release.get("signature")
            if (not isinstance(certificate, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", certificate)
                    or certificate.lower() != str(info["certificate_sha256"]).lower()):
                raise DownloadError("Missing or inconsistent RuStore signing certificate.")
            urls = release.get("downloadUrls") or []
            if len(urls) != 1:
                raise DownloadError("No standalone APK available. Split bundles are not supported yet.")
            entry = urls[0]
            url = httpx.URL(entry["url"])
            if url.scheme != "https" or url.host != "static.rustore.ru" or url.port not in (None, 443) or url.userinfo:
                raise DownloadError("Unexpected download host; only https://static.rustore.ru is supported.")
            size = int(entry["size"])
            if size <= 0 or size > max_mib * 1024**2:
                raise DownloadError(f"APK size {size} exceeds the configured limit or is invalid.")
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / f"{package}-{info['version_code']}.apk"
            if target.is_file() and not target.is_symlink() and target.stat().st_size == size:
                try:
                    checked = verify_apk(target, package, info["version_code"], certificate, aapt, apksigner)
                except DownloadError:
                    pass  # Re-fetch corrupt or mismatched cache entries.
                else:
                    return {**info, **checked, "path": str(target), "bytes": size, "cached": True}
            fd, filename = tempfile.mkstemp(dir=self.directory, suffix=".part")
            partial = Path(filename)
            try:
                digest_size = 0
                deadline = time.monotonic() + 300
                with os.fdopen(fd, "wb") as output, client.stream("GET", url, timeout=30) as response:
                    if response.status_code in (403, 429):
                        raise RateLimited(f"CDN HTTP {response.status_code}; stopped without retries.")
                    if response.status_code != 200:
                        raise DownloadError(f"CDN HTTP {response.status_code}; redirects are not followed.")
                    for chunk in response.iter_bytes(1024 * 1024):
                        digest_size += len(chunk)
                        if digest_size > size or time.monotonic() > deadline:
                            raise DownloadError("Download exceeded expected size or five-minute deadline.")
                        output.write(chunk)
                if digest_size != size:
                    raise DownloadError(f"Incomplete APK: expected {size} bytes, received {digest_size}.")
                checked = verify_apk(partial, package, info["version_code"], certificate, aapt, apksigner)
                os.replace(partial, target)
                return {**info, **checked, "path": str(target), "bytes": size, "cached": False}
            finally:
                partial.unlink(missing_ok=True)
