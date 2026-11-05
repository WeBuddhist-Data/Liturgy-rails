#!/usr/bin/env python3
"""Remove the H1 title from every uploaded edition's content on the WeBuddhist library.

Policy (2026-09-19): heading titles are not stored in the content — they belong
to the table of contents. Every edition uploaded before that policy carries its
title as the first line of `content` plus a `type: title` segment at reference
`0`. This script deletes both, in place, with one request per edition:

    PATCH /v2/editions/{id}/content  {"type": "delete", "start": 0, "end": N}

Proved live 2026-09-19 (see 4-SYSTEM/title-removal-test.md): the op removes the
title text, DETACH-deletes the title segment, and drops only that segment's
`0->0` alignment pair. Surviving segments keep their node ids, references, types
and line counts; their spans shift by exactly -N. Alignment links survive.

THE GUARD. A second patch on an already-cleaned edition would delete the *next*
segment's text. So every edition is re-checked immediately before it is patched:
it is touched only when its first segment is `type == "title"` starting at
character 0. That makes the run idempotent and safely resumable — a killed run
is restarted with the same command.

Usage (from the vault root):

    remove_title_segments.py --dry-run            what would be patched
    remove_title_segments.py                      patch everything outstanding
    remove_title_segments.py --only <stem>        one text (root + translations)
    remove_title_segments.py --exclude <stem>     skip a text entirely
    remove_title_segments.py --limit N            stop after N editions

Ledger: 4-SYSTEM/title-removal-ledger.json, written after every call.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "https://library.webuddhist.com"
HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.dirname(os.path.dirname(HERE))
LEDGER = os.path.join(HERE, "upload_ledger.json")
OUT = os.path.join(VAULT, "4-SYSTEM", "title-removal-ledger.json")
LANGS = ("en", "zh", "hi", "ne", "mn", "vi")

_lock = threading.Lock()


def _req(method, path, body=None, timeout=90, attempts=4):
    """One request. GETs are retried on timeouts and 5xx; writes NEVER are.

    A content PATCH is not idempotent: a delete op whose response was lost in
    transit has still been applied, and re-sending it deletes the *next* 27
    characters instead. That happened once on 2026-09-19 (edition
    kIBLisYhjyIOw2NUno2Ul) before this rule existed. A write that fails here
    is left for the operator: re-running the script re-reads the first segment
    and decides again, which is the only safe way to resolve it.
    """
    idempotent = method == "GET"
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(attempts):
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        key = os.environ.get("WEBUDDHIST_API_KEY", "")
        if key:
            req.add_header("X-API-Key", key)
        app = os.environ.get("WEBUDDHIST_APP", "")
        if app:
            req.add_header("X-Application", app)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            if idempotent and e.code >= 500 and attempt < attempts - 1:
                last = e
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if idempotent and attempt < attempts - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last  # unreachable


def first_segments(edition_id, n=2):
    _, d = _req("GET", f"/v2/editions/{edition_id}/segmentation/segments?limit={n}&offset=0")
    return d["items"]


def segment_text(segment_id):
    try:
        _, d = _req("GET", f"/v2/segments/{segment_id}/content")
        return d if isinstance(d, str) else (d or {}).get("content", "")
    except Exception:
        return ""


def targets(only=None, exclude=()):
    """Every uploaded edition in the ledger, as (stem, lang, edition_id)."""
    led = json.load(open(LEDGER))
    out = []
    for key, rec in sorted(led.items()):
        tail = key.rsplit("-", 1)[-1]
        stem, lang = (key[: -len(tail) - 1], tail) if tail in LANGS else (key, "bo")
        if only and stem != only:
            continue
        if stem in exclude:
            continue
        ed = rec.get("edition_id")
        if not ed or rec.get("deleted"):
            continue
        out.append((stem, lang, ed))
    return out


def load_out():
    if os.path.exists(OUT):
        return json.load(open(OUT))
    return {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "editions": {}}


def save_out(state):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)


def process(item, state, dry_run):
    stem, lang, ed = item
    rec = {"stem": stem, "lang": lang, "edition_id": ed}
    try:
        segs = first_segments(ed, 2)
        if not segs:
            rec["status"] = "empty-segmentation"
            return rec
        head = segs[0]
        start = head["lines"][0]["start"]
        end = head["lines"][-1]["end"]
        if head["type"] != "title" or start != 0:
            rec["status"] = "skip-no-title"
            rec["first_segment"] = {"type": head["type"], "reference": head["reference"], "span": [start, end]}
            return rec
        rec["span"] = [start, end]
        rec["reference"] = head["reference"]
        rec["removed_text"] = segment_text(head["id"])
        nxt = segs[1] if len(segs) > 1 else None
        if dry_run:
            rec["status"] = "would-patch"
            return rec
        status, _ = _req("PATCH", f"/v2/editions/{ed}/content", {"type": "delete", "start": start, "end": end})
        rec["http"] = status
        after = first_segments(ed, 1)
        if not after:
            rec["status"] = "verify-failed-empty"
            return rec
        a0 = after[0]
        ok = a0["type"] != "title" and a0["lines"][0]["start"] == 0
        if nxt:
            ok = ok and a0["reference"] == nxt["reference"] and a0["lines"][0]["end"] == nxt["lines"][0]["end"] - end
        rec["status"] = "removed" if ok else "verify-mismatch"
        rec["now_first"] = {"type": a0["type"], "reference": a0["reference"],
                            "span": [a0["lines"][0]["start"], a0["lines"][-1]["end"]]}
    except urllib.error.HTTPError as e:
        rec["status"] = "http-error"
        rec["error"] = f"{e.code} {e.read().decode()[:160]}"
    except Exception as e:  # noqa: BLE001
        rec["status"] = "error"
        rec["error"] = repr(e)[:200]
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)

    items = targets(args.only, set(args.exclude))
    state = load_out()
    done = {k for k, v in state["editions"].items() if v.get("status") in ("removed", "skip-no-title")}
    items = [i for i in items if i[2] not in done]
    if args.limit:
        items = items[: args.limit]
    print(f"{len(items)} editions to consider" + (" (dry run)" if args.dry_run else ""))

    counts = {}
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for rec in ex.map(lambda i: process(i, state, args.dry_run), items):
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            with _lock:
                state["editions"][rec["edition_id"]] = rec
                if not args.dry_run and len(state["editions"]) % 25 == 0:
                    save_out(state)
            if rec["status"] not in ("removed", "would-patch", "skip-no-title"):
                print(f"  !! {rec['stem'][:34]} [{rec['lang']}] {rec['status']} {rec.get('error','')}")
    if not args.dry_run:
        state["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_out(state)
    print("result:", json.dumps(counts, ensure_ascii=False))
    return 0 if not {k for k in counts if k not in ("removed", "would-patch", "skip-no-title")} else 1


if __name__ == "__main__":
    sys.exit(main())
