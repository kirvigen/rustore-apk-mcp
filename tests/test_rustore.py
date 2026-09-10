import io
import json
import zipfile

import httpx
import pytest

from apk_scrapper import rustore
from apk_scrapper.rustore import DownloadError, RateLimited, RuStore, validate_package, verify_apk

PACKAGE = "com.yolo_price_mobile"
CERT = "a" * 64


def apk_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("AndroidManifest.xml", "test fixture, not a real Android binary manifest")
    return stream.getvalue()


def setup_store(tmp_path, monkeypatch, *, response_bytes=None, url=None, status=200, info_override=None):
    payload = apk_bytes()
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.startswith("/applicationData/overallInfo"):
            body = {"packageName": PACKAGE, "appId": 123, "appName": "Example", "versionName": "1.0",
                    "versionCode": 7, "signature": CERT, "price": 0, **(info_override or {})}
            return httpx.Response(status, json={"code": "OK", "body": body})
        if request.url.path.endswith("/v2/download-link"):
            assert json.loads(request.content)["withoutSplits"] is True
            return httpx.Response(200, json={"code": "OK", "body": {
                "versionCode": 7, "signature": CERT, "downloadUrls": [{
                    "url": url or "https://static.rustore.ru/example.apk", "size": len(payload)}]}})
        assert request.url.host == "static.rustore.ru"
        return httpx.Response(200, content=payload if response_bytes is None else response_bytes)
    monkeypatch.setattr(rustore, "sdk_tool", lambda name: name)
    def fake_tool(command):
        if command[0] == "aapt":
            return f"package: name='{PACKAGE}' versionCode='7' versionName='1.0'"
        return f"Signer #1 certificate SHA-256 digest: {CERT}"
    monkeypatch.setattr(rustore, "run_tool", fake_tool)
    return RuStore(tmp_path, transport=httpx.MockTransport(handler), delay=0), calls


@pytest.mark.parametrize("value", ["../evil", "com.x/../../x", "https://example.org", "com.й", "x", "com.x\n"])
def test_invalid_packages(value):
    with pytest.raises(DownloadError):
        validate_package(value)


def test_download_and_verified_cache(tmp_path, monkeypatch):
    store, calls = setup_store(tmp_path, monkeypatch)
    first = store.download(PACKAGE)
    second = store.download(PACKAGE)
    assert not first["cached"] and second["cached"]
    assert first["sha256"] == second["sha256"]
    assert len([c for c in calls if c.url.host == "static.rustore.ru"]) == 1
    assert not list(tmp_path.glob("*.part"))


def test_corrupt_cache_redownloaded(tmp_path, monkeypatch):
    store, calls = setup_store(tmp_path, monkeypatch)
    store.download(PACKAGE)
    target = tmp_path / f"{PACKAGE}-7.apk"
    target.write_bytes(b"x" * target.stat().st_size)
    assert not store.download(PACKAGE)["cached"]
    assert len([c for c in calls if c.url.host == "static.rustore.ru"]) == 2


@pytest.mark.parametrize("payload", [b"short", b"<html>challenge</html>", b"x" * 10000])
def test_bad_download_never_published(tmp_path, monkeypatch, payload):
    store, _ = setup_store(tmp_path, monkeypatch, response_bytes=payload)
    with pytest.raises(DownloadError):
        store.download(PACKAGE)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("url", ["https://evil.test/a.apk", "http://static.rustore.ru/a.apk",
                                  "https://static.rustore.ru.evil.test/a.apk"])
def test_unexpected_host_rejected(tmp_path, monkeypatch, url):
    store, calls = setup_store(tmp_path, monkeypatch, url=url)
    with pytest.raises(DownloadError, match="host"):
        store.download(PACKAGE)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limit_not_retried(tmp_path, monkeypatch, status):
    store, calls = setup_store(tmp_path, monkeypatch, status=status)
    with pytest.raises(RateLimited):
        store.download(PACKAGE)
    assert len(calls) == 1


@pytest.mark.parametrize("override", [{"price": 5}, {"price": None}, {"aggregatorInfo": {"name": "external"}}])
def test_unsupported_listing_stops_before_download(tmp_path, monkeypatch, override):
    store, calls = setup_store(tmp_path, monkeypatch, info_override=override)
    with pytest.raises(DownloadError):
        store.download(PACKAGE)
    assert len(calls) == 1


def test_wrong_certificate_not_published(tmp_path, monkeypatch):
    store, _ = setup_store(tmp_path, monkeypatch)
    original = rustore.run_tool
    monkeypatch.setattr(rustore, "run_tool", lambda c: original(c) if c[0] == "aapt" else "invalid certificate")
    with pytest.raises(DownloadError, match="certificate"):
        store.download(PACKAGE)
    assert list(tmp_path.iterdir()) == []


def test_wrong_package_rejected(tmp_path, monkeypatch):
    path = tmp_path / "sample.apk"
    path.write_bytes(apk_bytes())
    monkeypatch.setattr(rustore, "run_tool", lambda c: "package: name='com.wrong' versionCode='7' versionName='1'")
    with pytest.raises(DownloadError, match="package"):
        verify_apk(path, PACKAGE, 7, CERT, "aapt", "apksigner")
