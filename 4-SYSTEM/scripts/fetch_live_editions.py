"""Fetch live content + segments for all 93 Tibetan editions (read-only)."""
import json, os, sys, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = "https://library.webuddhist.com"
VAULT = "/Users/tashitsering/Desktop/work/Obsidian/Liturgy-rails"
LEDGER = os.path.join(VAULT, "4-SYSTEM/scripts/upload_ledger.json")
OUT = sys.argv[1]

LANGS = ("en","zh","hi","ne","mn","vi")
led = json.load(open(LEDGER, encoding="utf-8"))
roots = {k: v for k, v in led.items() if k.rsplit("-",1)[-1] not in LANGS}

def get(path):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

def segments(ed):
    out, off = [], 0
    while True:
        r = get(f"/v2/editions/{ed}/segmentation/segments?limit=200&offset={off}")
        out.extend(r["items"])
        if not r.get("has_more"): break
        off += 200
    return out

def one(item):
    stem, rec = item
    ed = rec.get("edition_id")
    if not ed:
        return stem, {"error": "no edition_id"}
    try:
        return stem, {"edition_id": ed, "text_id": rec.get("text_id"),
                      "content": get(f"/v2/editions/{ed}/content"),
                      "segments": segments(ed),
                      "alignments": get(f"/v2/editions/{ed}/alignments")}
    except Exception as e:
        return stem, {"edition_id": ed, "error": f"{type(e).__name__}: {e}"}

with ThreadPoolExecutor(max_workers=8) as ex:
    res = dict(ex.map(one, roots.items()))

json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
bad = {k: v["error"] for k, v in res.items() if "error" in v}
print(f"fetched {len(res)} editions, errors {len(bad)}")
for k, v in bad.items(): print("  ERR", k, v)
