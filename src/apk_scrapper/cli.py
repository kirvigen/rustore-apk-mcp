import argparse
import json
import sys
from pathlib import Path
from urllib.error import URLError

from .core import Client, ScrapeError, parse_page


def main():
    parser = argparse.ArgumentParser(description="APKPure: parse pages and download direct APK/XAPK links")
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=30)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("info", "links"):
        p = commands.add_parser(command)
        p.add_argument("url", help="APKPure page URL")
        p.add_argument("--html", type=Path, help="Parse saved HTML instead of making an HTTP request")
    p = commands.add_parser("download", help="Download a direct binary URL; does not resolve page buttons")
    p.add_argument("url")
    p.add_argument("--directory", type=Path, default=Path("downloads"))
    p.add_argument("--max-mib", type=int, default=1024)
    args = parser.parse_args()
    try:
        client = Client(args.delay, args.timeout)
        if args.command == "download":
            result = client.download(args.url, args.directory, args.max_mib * 1024 * 1024)
        else:
            result = parse_page(args.html.read_text(encoding="utf-8"), args.url) if args.html else client.page(args.url)
            if args.command == "links":
                result = result["links"]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ScrapeError, OSError, URLError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
