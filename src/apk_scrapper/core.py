from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ScrapeError(Exception):
    pass


def checked_url(url: str, *, binary: bool = False) -> str:
    p = urlsplit(url)
    roots = ("apkpure.com", "apkpure.net", "pureapk.com", "winudf.com") if binary else ("apkpure.com",)
    if (p.scheme != "https" or p.username or p.password or p.port not in (None, 443)
            or not any(p.hostname == root or (p.hostname or "").endswith("." + root) for root in roots)):
        raise ScrapeError(f"URL outside allowed HTTPS hosts: {url}")
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, binary: bool):
        self.binary = binary

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl, binary=self.binary)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


PACKAGE = re.compile(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+")


def package_from_url(url: str) -> str | None:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    return next((part for part in reversed(parts) if PACKAGE.fullmatch(part)), None)


class PageParser(HTMLParser):
    """Parse links and schema.org metadata without relying on CSS class names."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self.schemas: list = []
        self.meta: dict[str, str] = {}
        self._anchor = None
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._anchor = {"href": attrs["href"], "text": ""}
            self.links.append(self._anchor)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.meta[key] = attrs["content"]
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._script = ""

    def handle_data(self, data):
        if self._script is not None:
            self._script += data
        elif self._anchor is not None:
            self._anchor["text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self._anchor = None
        if tag == "script" and self._script is not None:
            try:
                self.schemas.append(json.loads(self._script))
            except ValueError:
                pass
            self._script = None


def schema_apps(value):
    if isinstance(value, list):
        for item in value:
            yield from schema_apps(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if any(t in ("SoftwareApplication", "MobileApplication", "VideoGame") for t in types):
            yield value
        if "@graph" in value:
            yield from schema_apps(value["@graph"])


def parse_page(html: str, url: str) -> dict:
    checked_url(url)
    if any(marker in html.lower() for marker in ("cf-chl-", "just a moment...", "challenges.cloudflare.com")):
        raise ScrapeError("Cloudflare challenge received instead of application data")
    parser = PageParser()
    parser.feed(html)
    apps = list(schema_apps(parser.schemas))
    links = []
    seen = set()
    for link in parser.links:
        try:
            href = checked_url(urljoin(url, link["href"]), binary=True)
        except (ScrapeError, ValueError):
            continue
        if href not in seen:
            seen.add(href)
            links.append({"url": href, "text": " ".join(link["text"].split()), "package": package_from_url(href)})
    return {"url": url, "package": package_from_url(url),
            "name": (apps[0].get("name") if apps else None) or parser.meta.get("og:title"),
            "version": apps[0].get("softwareVersion") if apps else None,
            "description": (apps[0].get("description") if apps else None) or parser.meta.get("og:description"),
            "applications": apps, "links": links}


class Client:
    def __init__(self, delay: float = 2, timeout: float = 30):
        if delay < 0 or timeout <= 0:
            raise ScrapeError("delay must be nonnegative and timeout positive")
        self.delay, self.timeout = delay, timeout
        self._last = 0.0

    def open(self, url: str, *, binary: bool = False):
        checked_url(url, binary=binary)
        time.sleep(max(0, self.delay - (time.monotonic() - self._last)))
        self._last = time.monotonic()
        try:
            response = build_opener(SafeRedirect(binary)).open(
                Request(url, headers={"User-Agent": "apk-scrapper/0.1", "Accept-Encoding": "identity"}),
                timeout=self.timeout)
        except HTTPError as exc:
            exc.close()
            if exc.code in (403, 429):
                raise ScrapeError(f"HTTP {exc.code}: access blocked or rate limited; stopping without retries") from exc
            raise ScrapeError(f"HTTP {exc.code}: {url}") from exc
        if response.headers.get("cf-mitigated") == "challenge":
            response.close()
            raise ScrapeError("Cloudflare challenge; stopping")
        return response

    def page(self, url: str) -> dict:
        with self.open(url) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ScrapeError("HTML exceeds 8 MiB")
            return parse_page(raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace"), response.url)

    def download(self, url: str, directory: Path, max_bytes: int = 1024 ** 3) -> dict:
        """Download a direct file URL, validate ZIP structure, then publish atomically."""
        checked_url(url, binary=True)
        if max_bytes <= 0:
            raise ScrapeError("max_bytes must be positive")
        directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(suffix=".part", dir=directory)
        temporary = Path(temporary)
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as output, self.open(url, binary=True) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise ScrapeError("File exceeds size limit")
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ScrapeError("File exceeds size limit")
                    digest.update(chunk)
                    output.write(chunk)
                if declared and size != int(declared):
                    raise ScrapeError("Incomplete download")
            try:
                with zipfile.ZipFile(temporary) as archive:
                    names = set(archive.namelist())
                    if "AndroidManifest.xml" in names:
                        extension = "apk"
                    elif "manifest.json" in names and any(n.endswith(".apk") for n in names):
                        extension = "xapk"
                    else:
                        raise ScrapeError("ZIP does not contain APK/XAPK structure")
            except zipfile.BadZipFile as exc:
                raise ScrapeError("Response is not APK/XAPK (possibly an HTML challenge)") from exc
            sha256 = digest.hexdigest()
            target = directory / f"{sha256}.{extension}"
            os.replace(temporary, target)
            return {"path": str(target.resolve()), "sha256": sha256, "bytes": size, "format": extension, "url": url}
        finally:
            temporary.unlink(missing_ok=True)
