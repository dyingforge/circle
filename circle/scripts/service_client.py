"""Authenticated JSON client for Circle Service. Tokens never go in CLI arguments."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", type=Path, help="otherwise use CIRCLE_SERVICE_TOKEN")
    parser.add_argument("--path", default="/v1/tasks")
    parser.add_argument("--body", type=Path, help="POST this UTF-8 JSON file; otherwise GET")
    args = parser.parse_args()
    target = urllib.parse.urlsplit(args.url)
    if target.scheme not in {"https", "http"} or target.username or target.password or target.query or target.fragment:
        parser.error("use an http(s) server URL without credentials, query or fragment")
    if target.scheme == "http" and target.hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("remote connections require HTTPS")
    if not args.path.startswith("/v1/") or "?" in args.path or "#" in args.path:
        parser.error("path must start with /v1/ and have no query or fragment")
    token = args.token_file.read_text(encoding="utf-8").strip() if args.token_file else os.environ.get("CIRCLE_SERVICE_TOKEN", "")
    if not token:
        parser.error("provide --token-file or CIRCLE_SERVICE_TOKEN")
    body = None
    if args.body:
        body = json.dumps(json.loads(args.body.read_text(encoding="utf-8")), ensure_ascii=False).encode()
    request = urllib.request.Request(args.url.rstrip("/") + args.path, data=body,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    # Never forward bearer tokens to a redirect destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            print(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
