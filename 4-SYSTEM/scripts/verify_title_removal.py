#!/usr/bin/env python3
"""Verify the title-removal sweep against the payloads that were uploaded.

For every edition in the upload ledger, checks three things:

    content    live content == payload content with the title span cut off
    segments   no `type: title` segment left; references identical to the
               payload's minus its first; every span shifted by exactly -N
    alignments the root's pair lists carry no `0` reference any more

The payloads are the only independent record of what was uploaded, so this is
a real check rather than a restatement of the sweep's own ledger.

    verify_title_removal.py [--exclude <stem>] [--workers N]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://library.webuddhist.com"
HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.dirname(os.path.dirname(HERE))
LEDGER = os.path.join(HERE, "upload_ledger.json")
PAY_ROOT = os.path.join(HERE, "payloads")
PAY_TR = os.path.join(HERE, "payloads-translations")
LANGS = ("en", "zh", "hi", "ne", "mn", "vi")


def get(path, attempts=4):
    import time
    for a in range(attempts):
        try:
            with urllib.request.urlopen(BASE + path, timeout=90) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw.lstrip()[:1] in "[{\"" else raw
        except (urllib.error.URLError, TimeoutError, OSError):
            if a == attempts - 1:
                raise
            time.sleep(1.5 * (a + 1))


def segments(edition_id):
    out, off = [], 0
    while True:
        d = get(f"/v2/editions/{edition_id}/segmentation/segments?limit=500&offset={off}")
        out += d["items"]
        if not d.get("has_more"):
            return out
        off += len(d["items"])


def payload_for(stem, lang):
    path = (os.path.join(PAY_ROOT, f"{stem}.edition.json") if lang == "bo"
            else os.path.join(PAY_TR, f"{stem}-{lang}.edition.json"))
    if not os.path.exists(path):
        return None
    return json.load(open(path))


def check(item):
    stem, lang, ed = item
    r = {"stem": stem, "lang": lang, "edition_id": ed, "problems": []}
    live_segs = segments(ed)
    live = get(f"/v2/editions/{ed}/content")
    if any(s["type"] == "title" for s in live_segs):
        r["problems"].append("title segment still present")
    if live_segs and live_segs[0]["lines"][0]["start"] != 0:
        r["problems"].append("first segment does not start at 0")
    p = payload_for(stem, lang)
    if p is None:
        r["payload"] = False
        return r
    r["payload"] = True
    pay = p["segmentation"]["segments"]
    if pay[0]["type"] != "title":
        r["problems"].append("payload has no leading title segment")
        return r
    n = pay[0]["lines"][-1]["end"]
    if live != p["content"][n:]:
        r["problems"].append(
            f"content mismatch: live {len(live)} chars, expected {len(p['content'][n:])}")
    exp_refs = [s["reference"] for s in pay[1:]]
    if [s["reference"] for s in live_segs] != exp_refs:
        r["problems"].append("references differ from payload")
    exp_spans = [[s["lines"][0]["start"] - n, s["lines"][-1]["end"] - n] for s in pay[1:]]
    got_spans = [[s["lines"][0]["start"], s["lines"][-1]["end"]] for s in live_segs]
    if got_spans != exp_spans:
        r["problems"].append("spans not shifted by exactly -%d" % n)
    if [len(s["lines"]) for s in live_segs] != [len(s["lines"]) for s in pay[1:]]:
        r["problems"].append("line counts differ from payload")
    return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)

    led = json.load(open(LEDGER))
    items, roots = [], []
    for key, rec in sorted(led.items()):
        tail = key.rsplit("-", 1)[-1]
        stem, lang = (key[: -len(tail) - 1], tail) if tail in LANGS else (key, "bo")
        if stem in set(args.exclude) or not rec.get("edition_id") or rec.get("deleted"):
            continue
        items.append((stem, lang, rec["edition_id"]))
        if lang == "bo":
            roots.append((stem, rec["edition_id"]))

    bad, nopay = [], []
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for r in ex.map(check, items):
            if r["problems"]:
                bad.append(r)
                print(f"  !! {r['stem'][:36]} [{r['lang']}] {'; '.join(r['problems'])}")
            if not r.get("payload"):
                nopay.append(r)
    print(f"editions checked: {len(items)} | content+segments verified against payload: "
          f"{len(items)-len(nopay)} | no payload on disk: {len(nopay)} | problems: {len(bad)}")

    stale = 0
    def pairs_ok(arg):
        stem, root = arg
        out = []
        for link in get(f"/v2/editions/{root}/alignments"):
            tgt = link["target_edition_id"]
            ps = []
            off = 0
            while True:
                d = get(f"/v2/editions/{root}/alignments/{tgt}?limit=500&offset={off}")
                ps += d["items"]
                if not d.get("has_more"):
                    break
                off += len(d["items"])
            if any(x["source_segment"]["reference"] == "0" or x["target_segment"]["reference"] == "0" for x in ps):
                out.append((stem, tgt, len(ps)))
        return out
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for res in ex.map(pairs_ok, roots):
            for stem, tgt, n in res:
                stale += 1
                print(f"  !! {stem[:36]} alignment {tgt} still has a '0' pair ({n} pairs)")
    print(f"alignment pair lists checked on {len(roots)} roots | lists still carrying a '0' reference: {stale}")
    return 1 if (bad or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
