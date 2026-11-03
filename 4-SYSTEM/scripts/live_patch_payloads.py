"""Build PATCH /v2/editions/{id}/content payloads taking each LIVE edition to
the state its 1-SOURCES note now describes, under two rules:

  * heading lines (#, ##, ###) are table-of-contents material, never content
  * <small>…</small> marks a yigchung annotation: the tags are not content,
    the text between them is

`old` is the live edition (content + its stored line spans, in stored order).
`new` is built from the source note. The ops are reduced to minimal
insert/delete/replace and then the backend's OWN span arithmetic is replayed
over the live spans; a payload is emitted only if the replayed spans come out
exactly equal to the spans the new build produces. Anything that fails that
replay is reported as needing a re-segmentation instead.

Read-only: writes payload JSON and a report, calls no mutating endpoint.
"""
import importlib.util, json, os, re, sys, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import new_content_rules as T

V = T.VAULT
HERE = os.path.dirname(os.path.abspath(__file__))
SCR = os.environ.get("LIVE_PATCH_DIR", HERE)

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.path.insert(0, os.path.dirname(path))
    spec.loader.exec_module(m); return m

CP = load("cpp", os.path.join(HERE, "content_patch_payloads.py"))
LP = load("lp",  os.path.join(HERE, "liturgy_payloads.py"))
bid = load("bid", os.path.join(HERE, "block_ids.py"))

def norm(s): return re.sub(r"[\s།]+$", "", unicodedata.normalize("NFC", s))

def build_new(src_path):
    """Content + segmentation for a source note under the two rules."""
    raw = open(src_path, encoding="utf-8").read()
    _, body = bid.split_frontmatter(raw, src_path)
    stripped = bid.strip_text(raw, src_path)
    _, plain = bid.split_frontmatter(stripped, src_path)
    blocks, heads = T.units(plain)
    _, units = bid.parse_body(body)
    bid.assign_ids(units)
    refs = [u.bid for u in units if u.kind == "segment"]
    if len(refs) != len(blocks):
        raise ValueError(f"id/block mismatch {len(refs)} vs {len(blocks)}")
    parts, segs, pos = [], [], 0
    for b, ref in zip(blocks, refs):
        spans = []
        for l in b["lines"]:
            spans.append({"start": pos, "end": pos + len(l)})
            parts.append(l); pos += len(l)
        segs.append({"lines": spans, "type": "verse", "reference": ref})
    content = "".join(parts)
    ed = {"content": content, "segmentation": {"segments": segs}}
    LP.retype_segments(ed)
    return ed, heads

live = json.load(open(os.path.join(SCR, "live-editions.json"), encoding="utf-8"))
srcs = {norm(os.path.basename(p)[:-3]): p
        for p in __import__("glob").glob(V + "/1-SOURCES/Text/*.md")}

results = []
for stem, L in sorted(live.items()):
    r = {"stem": stem, "edition_id": L["edition_id"], "n_align": len(L["alignments"] or [])}
    try:
        new, heads = build_new(srcs[norm(stem)])
    except Exception as e:
        r["class"] = "BUILD_FAIL"; r["error"] = f"{type(e).__name__}: {e}"; results.append(r); continue

    old_text = L["content"]
    old_spans = [(l["start"], l["end"]) for s in L["segments"] for l in s["lines"]]
    # which live segment each flat span belongs to — the backend never re-groups
    # line spans, so a segment SPLIT can preserve the flat span list while the
    # segmentation itself is wrong. Group membership must be checked too.
    old_owner = [si for si, s in enumerate(L["segments"]) for _ in s["lines"]]
    old_shape = [(s["reference"], len(s["lines"])) for s in L["segments"]]
    new_shape = [(s["reference"], len(s["lines"])) for s in new["segmentation"]["segments"]]
    new_spans = CP.flat_spans(new)
    r["old_refs"] = [s["reference"] for s in L["segments"]]
    r["new_refs"] = [s["reference"] for s in new["segmentation"]["segments"]]
    r["refs_equal"] = r["old_refs"] == r["new_refs"]
    r["n_sections"] = len([h for h in heads if h["level"] >= 2])

    old_seg_shape = [len(s["lines"]) for s in L["segments"]]
    new_seg_shape0 = [len(s["lines"]) for s in new["segmentation"]["segments"]]
    if old_text == new["content"]:
        r["n_ops"] = 0
        if old_seg_shape == new_seg_shape0:
            r["class"] = "NO_CONTENT_CHANGE"
        else:
            # same characters, different segment boundaries: the expert split or
            # merged a block. No content op can express that.
            r["class"] = "RESEGMENT"
            r["why"] = (f"text identical but segments {len(old_seg_shape)} -> "
                        f"{len(new_seg_shape0)} (a split/merge, no content change)")
            r["old_shape"], r["new_shape"] = old_seg_shape, new_seg_shape0
        results.append(r); continue

    ops = CP.make_ops(old_text, new["content"])
    text, spans = old_text, list(old_spans)
    for op in ops:
        spans = CP.apply_op_to_spans(spans, op)
        text = CP.apply_op_to_text(text, op)
    survived = [s for s in spans if s is not None]
    r["n_ops"] = len(ops)
    r["text_ok"] = (text == new["content"])
    # replayed segmentation: per live segment, the spans of it that survived
    replayed = {}
    for sp, si in zip(spans, old_owner):
        if sp is not None:
            replayed.setdefault(si, []).append(sp)
    replayed_shape = [len(replayed[si]) for si in sorted(replayed)]
    new_seg_shape = [len(s["lines"]) for s in new["segmentation"]["segments"]]
    r["replayed_shape"] = replayed_shape
    r["new_seg_shape"] = new_seg_shape
    r["spans_ok"] = (survived == new_spans and replayed_shape == new_seg_shape)
    if r["text_ok"] and r["spans_ok"]:
        r["class"] = "PATCH"
        r["ops"] = ops
        r["dropped_spans"] = sum(1 for s in spans if s is None)
    else:
        r["class"] = "RESEGMENT"
        if not r["text_ok"]:
            r["why"] = "content replay mismatch"
        elif survived != new_spans:
            r["why"] = f"line spans {len(survived)} != {len(new_spans)} after replay"
        else:
            r["why"] = (f"segment shape {len(replayed_shape)} segs {replayed_shape[:6]}… "
                        f"!= {len(new_seg_shape)} segs {new_seg_shape[:6]}… (a split/merge)")
        r["old_shape"], r["new_shape"] = old_shape, new_shape
    results.append(r)

os.makedirs(os.path.join(SCR, "payloads-live-patch"), exist_ok=True)
for r in results:
    if r["class"] == "PATCH":
        json.dump({"edition_id": r["edition_id"], "stem": r["stem"], "ops": r["ops"]},
                  open(os.path.join(SCR, "payloads-live-patch", r["stem"] + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
json.dump(results, open(os.path.join(SCR, "live-patch-report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

import collections
c = collections.Counter(r["class"] for r in results)
print("=== against the LIVE backend, with the new source notes ===")
for k in ("NO_CONTENT_CHANGE", "PATCH", "RESEGMENT", "BUILD_FAIL"): print(f"  {k:<20} {c.get(k,0)}")
print(f"\n  block ids equal to live refs : {sum(1 for r in results if r.get('refs_equal'))}/93")
print(f"  texts with ## sections (TOC) : {sum(1 for r in results if r.get('n_sections'))}")
print(f"\n  total PATCH requests to send : {sum(r['n_ops'] for r in results if r['class']=='PATCH')}")
print("\n=== PATCH ===")
for r in results:
    if r["class"] == "PATCH":
        print(f"  {r['n_ops']:>3} ops, {r['dropped_spans']} line-span(s) dropped, {r['n_align']} links  {r['stem'][:55]}")
print("\n=== RESEGMENT ===")
for r in results:
    if r["class"] == "RESEGMENT":
        print(f"  {r['n_ops']:>3} ops  {r['why']}  {r['n_align']} links  {r['stem'][:50]}")
print("\n=== BUILD_FAIL ===")
for r in results:
    if r["class"] == "BUILD_FAIL": print(f"  {r['stem'][:50]}: {r['error']}")
