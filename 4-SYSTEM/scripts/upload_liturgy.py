#!/usr/bin/env python3
"""Upload prepared liturgy payloads to the WeBuddhist v2 API.

Per text, two calls:

    POST /v2/texts                     <- <stem>.text.json      -> text_id
    POST /v2/texts/{text_id}/editions  <- <stem>.edition.json    -> edition_id

The edition body carries `content` and `segmentation` together, so the
separate segmentation endpoint is not used. No table-of-contents call:
these texts have no `##` sections.

Nothing about this is idempotent on the server — `POST /v2/texts` will
happily create a second copy of a text that is already there. The ledger
is what makes re-running safe: every id is written to it the moment the
server returns it, BEFORE the next call is made, so an interrupted run
resumes instead of duplicating, and a bad run can be cleaned up because
every id it created is on record.

Credentials come from the environment and are never written to disk:

    WEBUDDHIST_API_KEY   sent as X-API-Key       (required)
    WEBUDDHIST_APP       sent as X-Application   (optional; omitted if empty)

Usage:
    upload_liturgy.py --payloads <dir> [--only <stem>] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://library.webuddhist.com"
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "upload_ledger.json")


class UploadError(RuntimeError):
    pass


def load_ledger(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_ledger(path, ledger):
    # Written after every single call, not at the end of the run.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def post(url, body, key, app, timeout=60):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-Key", key)
    # Omitted rather than sent empty: `X-Application` is not declared in the
    # v2 schema for these operations — they are secured by `X-API-Key` alone.
    # This matches upload_translations.py, which uploaded all 646 translation
    # editions this way.
    if app:
        req.add_header("X-Application", app)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise UploadError(f"HTTP {exc.code} from {url}\n         {detail}") from None
    except urllib.error.URLError as exc:
        raise UploadError(f"network error for {url}: {exc.reason}") from None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--payloads", required=True)
    ap.add_argument("--only", help="upload just this stem")
    ap.add_argument("--limit", type=int, help="stop after N texts")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ledger", default=LEDGER)
    args = ap.parse_args(argv)

    key = os.environ.get("WEBUDDHIST_API_KEY", "")
    app = os.environ.get("WEBUDDHIST_APP", "")
    if not args.dry_run and not key:
        print("WEBUDDHIST_API_KEY must be set", file=sys.stderr)
        return 2

    stems = sorted({f.rsplit(".", 2)[0] for f in os.listdir(args.payloads)
                    if f.endswith(".json")})
    if args.only:
        if args.only not in stems:
            print(f"no payload for {args.only!r}", file=sys.stderr)
            return 2
        stems = [args.only]
    if args.limit:
        stems = stems[:args.limit]

    ledger = load_ledger(args.ledger)
    created = skipped = 0

    for stem in stems:
        entry = ledger.setdefault(stem, {})
        if entry.get("text_id") and entry.get("edition_id"):
            skipped += 1
            print(f"skip   {stem}  (text {entry['text_id']}, edition {entry['edition_id']})")
            continue

        with open(os.path.join(args.payloads, f"{stem}.text.json"), encoding="utf-8") as fh:
            text_body = json.load(fh)
        with open(os.path.join(args.payloads, f"{stem}.edition.json"), encoding="utf-8") as fh:
            edition_body = json.load(fh)

        n_seg = len(edition_body["segmentation"]["segments"])
        n_chr = len(edition_body["content"])

        if args.dry_run:
            print(f"DRY    {stem}")
            print(f"         POST {BASE}/v2/texts  "
                  f"({json.dumps(text_body, ensure_ascii=False)[:90]}…)")
            print(f"         POST {BASE}/v2/texts/<id>/editions  "
                  f"({n_chr} chars, {n_seg} segments)")
            continue

        try:
            if not entry.get("text_id"):
                _, res = post(f"{BASE}/v2/texts", text_body, key, app)
                entry["text_id"] = res["id"]
                save_ledger(args.ledger, ledger)      # persist before next call
                print(f"       {stem}\n         text_id    {res['id']}")

            if not entry.get("edition_id"):
                url = f"{BASE}/v2/texts/{entry['text_id']}/editions"
                _, res = post(url, edition_body, key, app)
                entry["edition_id"] = res["id"]
                save_ledger(args.ledger, ledger)
                print(f"         edition_id {res['id']}  "
                      f"({n_chr} chars, {n_seg} segments)")
            created += 1
        except UploadError as exc:
            save_ledger(args.ledger, ledger)
            print(f"FAIL   {stem}\n         {exc}", file=sys.stderr)
            print(f"\nstopped on first error. ledger: {args.ledger}", file=sys.stderr)
            return 1
        time.sleep(0.15)

    if args.dry_run:
        print(f"\ndry run: {len(stems)} texts, {len(stems) * 2} requests, nothing sent")
    else:
        print(f"\nuploaded {created}, skipped {skipped}. ledger: {args.ledger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
