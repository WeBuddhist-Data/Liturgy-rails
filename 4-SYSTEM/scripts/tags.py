#!/usr/bin/env python3
"""WeBuddhist tags: create from the registry and attach to texts.

    tags.py sync                       create every registry tag that has no id yet
    tags.py apply KEY --category CID   attach tag KEY to every text in category CID
    tags.py apply KEY --text TID ...   attach tag KEY to the listed texts
    tags.py verify KEY --category CID  compare ?tag_id= listing with the category listing

Registry: 4-SYSTEM/tag-registry.json (key -> {id, title, description}).
The backend tag model is flat, so the key is stored as the first line of the
tag description ("key: facet/value") and `sync` matches live tags on that line
before creating anything, so it is safe to re-run.

`apply` is diff-based: it reads every text's live tag_ids and only POSTs the
missing attachments. It never retries a failed write on its own.

Env: WEBUDDHIST_API_KEY (X-API-Key, optional), WEBUDDHIST_APP (X-Application,
default "webuddhist").
"""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

BASE = "https://library.webuddhist.com"
ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "4-SYSTEM" / "tag-registry.json"
LEDGER_DIR = ROOT / "4-SYSTEM" / "tag-ledgers"


def headers():
    h = {"X-Application": os.environ.get("WEBUDDHIST_APP") or "webuddhist"}
    key = os.environ.get("WEBUDDHIST_API_KEY")
    if key:
        h["X-API-Key"] = key
    return h


def call(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]


def load_registry():
    return json.loads(REGISTRY.read_text())


def save_registry(reg):
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n")


def key_line(key):
    return f"key: {key}"


def live_tags():
    st, tags = call("GET", "/v2/tags")
    if st != 200:
        sys.exit(f"GET /v2/tags -> {st} {tags}")
    by_key = {}
    for t in tags:
        desc = (t.get("description") or {})
        first = ""
        for v in desc.values():
            first = (v or "").splitlines()[0].strip() if v else ""
            if first.startswith("key: "):
                break
        if first.startswith("key: "):
            by_key[first[5:].strip()] = t["id"]
    return tags, by_key


def list_texts(category_id):
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"category_id": category_id, "limit": 100, "offset": offset})
        st, d = call("GET", f"/v2/texts?{q}")
        if st != 200:
            sys.exit(f"GET /v2/texts -> {st} {d}")
        out += d["items"]
        if not d.get("has_more"):
            return out
        offset += 100


def list_by_tag(tag_id):
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"tag_id": tag_id, "limit": 100, "offset": offset})
        st, d = call("GET", f"/v2/texts?{q}")
        if st != 200:
            sys.exit(f"GET /v2/texts?tag_id -> {st} {d}")
        out += d["items"]
        if not d.get("has_more"):
            return out
        offset += 100


def cmd_sync(_):
    reg = load_registry()
    _, by_key = live_tags()
    changed = False
    for key, spec in reg["tags"].items():
        if key in by_key:
            if spec.get("id") != by_key[key]:
                print(f"{key}: live id {by_key[key]} recorded")
                spec["id"] = by_key[key]; changed = True
            else:
                print(f"{key}: exists ({spec['id']})")
            continue
        body = {"title": spec["title"],
                "description": {"en": key_line(key) + "\n" + spec["description"]}}
        st, d = call("POST", "/v2/tags", body)
        if st != 201:
            sys.exit(f"{key}: POST /v2/tags -> {st} {d}")
        spec["id"] = d["id"]; changed = True
        print(f"{key}: created {d['id']}")
    if changed:
        save_registry(reg)


def resolve(key):
    spec = load_registry()["tags"].get(key)
    if not spec or not spec.get("id"):
        sys.exit(f"{key}: not in registry or not yet created (run sync)")
    return spec["id"]


def cmd_apply(a):
    tag_id = resolve(a.key)
    if a.category:
        texts = list_texts(a.category)
    else:
        texts = []
        for tid in a.text:
            st, d = call("GET", f"/v2/texts/{tid}")
            if st != 200:
                sys.exit(f"GET /v2/texts/{tid} -> {st} {d}")
            texts.append(d)
    todo = [t for t in texts if tag_id not in (t.get("tag_ids") or [])]
    print(f"{a.key} ({tag_id}): {len(texts)} texts, {len(texts)-len(todo)} already tagged, {len(todo)} to do")
    if a.limit:
        todo = todo[:a.limit]
    if a.dry_run:
        for t in todo:
            print("  would tag", t["id"], t.get("language"), next(iter(t["title"].values()), ""))
        return
    LEDGER_DIR.mkdir(exist_ok=True)
    ledger = LEDGER_DIR / f"{a.key.replace('/', '-')}.jsonl"
    ok = fail = 0
    with ledger.open("a") as lf:
        for t in todo:
            st, d = call("POST", f"/v2/texts/{t['id']}/tags/{tag_id}")
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "text_id": t["id"], "tag_id": tag_id, "status": st}
            if st == 204:
                ok += 1
            else:
                fail += 1; rec["error"] = d
                print(f"  FAIL {t['id']}: {st} {d}")
            lf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"done: {ok} attached, {fail} failed. ledger: {ledger.relative_to(ROOT)}")


def cmd_verify(a):
    tag_id = resolve(a.key)
    cat = {t["id"]: t for t in list_texts(a.category)}
    tagged = {t["id"]: t for t in list_by_tag(tag_id)}
    missing = [i for i in cat if i not in tagged]
    extra = [i for i in tagged if i not in cat]
    stale = [i for i, t in cat.items() if tag_id not in (t.get("tag_ids") or [])]
    print(f"category {a.category}: {len(cat)} texts; tagged {a.key}: {len(tagged)}")
    print(f"  in category but not tagged: {len(missing)}")
    print(f"  tagged but outside category: {len(extra)}")
    print(f"  category rows whose tag_ids lack the tag: {len(stale)}")
    for i in missing[:20]:
        print("   missing", i, cat[i].get("language"), next(iter(cat[i]["title"].values()), ""))


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = ap.add_subparsers(dest="cmd", required=True)
sub.add_parser("sync").set_defaults(fn=cmd_sync)
p = sub.add_parser("apply"); p.add_argument("key"); p.add_argument("--category"); p.add_argument("--text", nargs="*", default=[])
p.add_argument("--limit", type=int, default=0); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_apply)
p = sub.add_parser("verify"); p.add_argument("key"); p.add_argument("--category", required=True); p.set_defaults(fn=cmd_verify)
args = ap.parse_args()
if args.cmd == "apply" and not (args.category or args.text):
    ap.error("apply needs --category or --text")
args.fn(args)
