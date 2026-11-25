"""Segment mapping: which old segment (live on an environment) became which new one (the note).

Re-segmenting an edition (DELETE segmentation -> POST segmentation) gives every segment a new
id, and may change references and boundaries. Anything that pointed at an old segment — an
alignment pair, a bookmark, a transclusion `^ref` in another repo — has to be moved to a new
segment. This module judges how the two segmentations relate and builds that map.

**Every old segment maps to at most one new segment.** Several new segments cannot stand in
for one old one: when an old segment is split into five, it maps to the FIRST of them (where
its text now starts), and the other four are left unmapped. Several old segments may map to
the same new one (a merge).

The method is chosen from the data, never assumed:

  identical   same content, same spans, same references     nothing moves
  renumber    same content, same spans, references differ   1:1 by span: segment i -> segment i
  resegment   boundaries and/or content differ              by content: both contents are
              tokenised (Tibetan syllables / words; spaces and tsheg/shad punctuation ignored),
              the token sequences are diffed, and every matched token votes for the pair
              (old segment it sits in, new segment it lands in). Pairs carrying a real share of
              either side are overlaps; each old segment maps to the first new segment it
              overlaps.

What happened to each group of overlapping segments is reported as:

  1:1         one old -> one new (the text may be edited inside it)
  split       one old -> several new; mapped to the first part, the other parts `unmapped`
  merge       several old -> one new; each old maps to it
  regroup     several old <-> several new (boundaries moved); each old -> the first new segment
              it overlaps, new segments no old one chose are `unmapped`
  delete      an old segment whose text is not in the new content (old -> none)
  new         a new segment whose text was not in the old content (none -> new)

An overlap below MIN_SHARE of both its old and its new segment (a boundary moved by a syllable
or two) is `minor`: shown, never used.
"""
from __future__ import annotations

import bisect
import difflib
import re

MIN_SHARE = 0.2
TOKEN = re.compile(r"[^\s༄-༔༺-༽.,;:!?\"'()\[\]«»“”‘’—–-]+")
KINDS = ("1:1", "split", "merge", "regroup", "delete", "new")


def segs(segments, content=None) -> list:
    """API / payload segments -> [{id, ref, type, start, end, lines}] in content order."""
    out = []
    for s in segments or []:
        lines = s.get("lines") or []
        if not lines:
            continue
        out.append({"id": s.get("id"), "ref": str(s.get("reference")), "type": s.get("type"),
                    "start": min(l["start"] for l in lines), "end": max(l["end"] for l in lines),
                    "lines": [(l["start"], l["end"]) for l in lines]})
    out.sort(key=lambda x: x["start"])
    return out


def _tokens(text):
    return [(m.group(0), m.start()) for m in TOKEN.finditer(text)]


def _owner(seglist):
    """char position -> index of the segment whose lines cover it (None between segments)."""
    spans = sorted((a, b, i) for i, s in enumerate(seglist) for a, b in s["lines"])
    keys = [a for a, _, _ in spans]

    def find(pos):
        k = bisect.bisect_right(keys, pos) - 1
        if k >= 0 and spans[k][0] <= pos < spans[k][1]:
            return spans[k][2]
        return None
    return find


def _weights(old_content, old, new_content, new):
    """-> {(old index, new index): matched characters}, plus per-segment totals."""
    ot, nt = _tokens(old_content), _tokens(new_content)
    of, nf = _owner(old), _owner(new)
    w, o_tot, n_tot = {}, [0] * len(old), [0] * len(new)
    for tok, pos in ot:
        i = of(pos)
        if i is not None:
            o_tot[i] += len(tok)
    for tok, pos in nt:
        j = nf(pos)
        if j is not None:
            n_tot[j] += len(tok)
    sm = difflib.SequenceMatcher(None, [t for t, _ in ot], [t for t, _ in nt], autojunk=False)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            tok, op = ot[a + k]
            _, np_ = nt[b + k]
            i, j = of(op), nf(np_)
            if i is not None and j is not None:
                w[(i, j)] = w.get((i, j), 0) + len(tok)
    return w, o_tot, n_tot


def judge(old_content, old_segments, new_content, new_segments) -> dict:
    """-> the map: {"method", "old", "new", "edges", "groups", "target", "counts", "summary"}.
    `target[i]` is the one new index old segment i maps to, or None (deleted)."""
    old, new = old_segments, new_segments
    if old_content == new_content and [s["lines"] for s in old] == [s["lines"] for s in new]:
        same_refs = [s["ref"] for s in old] == [s["ref"] for s in new]
        edges = [{"old": i, "new": i, "chars": s["end"] - s["start"], "minor": False} for i, s in enumerate(old)]
        groups = [{"kind": "1:1", "old": [i], "new": [i]} for i in range(len(old))]
        return _finish("identical" if same_refs else "renumber", old, new, edges, groups, list(range(len(old))))

    w, o_tot, n_tot = _weights(old_content, old, new_content, new)
    edges = []
    for (i, j), c in sorted(w.items()):
        share = max(c / o_tot[i] if o_tot[i] else 0, c / n_tot[j] if n_tot[j] else 0)
        edges.append({"old": i, "new": j, "chars": c, "minor": share < MIN_SHARE})
    # one new segment per old one: the first new segment it overlaps (where its text now starts)
    target = [None] * len(old)
    for e in sorted((e for e in edges if not e["minor"]), key=lambda e: e["new"]):
        if target[e["old"]] is None:
            target[e["old"]] = e["new"]
    # connected groups over the non-minor overlaps, to say what happened
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        if not e["minor"]:
            parent[find(f"o{e['old']}")] = find(f"n{e['new']}")
    comp = {}
    for i in range(len(old)):
        comp.setdefault(find(f"o{i}"), {"old": [], "new": []})["old"].append(i)
    for j in range(len(new)):
        comp.setdefault(find(f"n{j}"), {"old": [], "new": []})["new"].append(j)
    groups = []
    for g in comp.values():
        no, nn = len(g["old"]), len(g["new"])
        kind = ("delete" if nn == 0 else "new" if no == 0 else "1:1" if no == nn == 1
                else "split" if no == 1 else "merge" if nn == 1 else "regroup")
        groups.append({"kind": kind, "old": sorted(g["old"]), "new": sorted(g["new"])})
    return _finish("resegment", old, new, edges, _in_text_order(groups), target)


def _in_text_order(groups):
    """Groups in reading order: by their first new segment; a deleted old segment goes right after
    the group that holds the old segment before it."""
    ordered = sorted((g for g in groups if g["new"]), key=lambda g: g["new"][0])
    for d in sorted((g for g in groups if not g["new"]), key=lambda g: g["old"][0]):
        at = next((k + 1 for k in range(len(ordered) - 1, -1, -1)
                   if ordered[k]["old"] and max(ordered[k]["old"]) < d["old"][0]), 0)
        ordered.insert(at, d)
    return ordered


def _finish(method, old, new, edges, groups, target):
    counts = {}
    for g in groups:
        counts[g["kind"]] = counts.get(g["kind"], 0) + 1
    unmapped = len(set(range(len(new))) - {t for t in target if t is not None}) - counts.get("new", 0)
    summary = f"{method}: " + ", ".join(f"{counts[k]} × {k}" for k in KINDS if k in counts)
    if unmapped:
        summary += f"; {unmapped} new segment(s) unmapped (parts of a split or regroup)"
    return {"method": method, "old": old, "new": new, "edges": edges, "groups": groups, "target": target,
            "counts": counts, "unmapped": unmapped, "summary": summary}


def ref_map(m) -> dict:
    """old reference -> [its one new reference], or [] when the old segment was deleted."""
    return {s["ref"]: ([m["new"][m["target"][i]]["ref"]] if m["target"][i] is not None else [])
            for i, s in enumerate(m["old"])}


def rows(m) -> list:
    """Report rows: one per old segment (-> its one new segment, or none), then every new
    segment no old one maps to (`new`, or `unmapped` when it is part of a split / regroup)."""
    kind_old, kind_new = {}, {}
    for g in m["groups"]:
        for i in g["old"]:
            kind_old[i] = g["kind"]
        for j in g["new"]:
            kind_new[j] = g["kind"]
    chars = {(e["old"], e["new"]): e["chars"] for e in m["edges"]}
    out = []
    for i, o in enumerate(m["old"]):
        j = m["target"][i]
        n = m["new"][j] if j is not None else {}
        out.append({"kind": kind_old.get(i, "delete") if j is not None else "delete",
                    "old_ref": o["ref"], "old_id": o.get("id"),
                    "new_ref": n.get("ref"), "new_id": n.get("id"), "chars": chars.get((i, j), 0)})
    chosen = {t for t in m["target"] if t is not None}
    for j, n in enumerate(m["new"]):
        if j not in chosen:
            k = kind_new.get(j, "new")
            out.append({"kind": "new" if k == "new" else "unmapped", "old_ref": None, "old_id": None,
                        "new_ref": n["ref"], "new_id": n.get("id"), "chars": 0})
    return out


def fill_new_ids(m, live_new_segments) -> int:
    """Copy the server's ids onto the map's new segments, matched by their line spans. -> how many."""
    by_span = {tuple(s["lines"]): s.get("id") for s in segs(live_new_segments)}
    n = 0
    for s in m["new"]:
        sid = by_span.get(tuple(s["lines"]))
        if sid:
            s["id"] = sid
            n += 1
    return n


def remap_pairs(pairs, rmap, side):
    """Move alignment pairs [(source ref, target ref)] through `rmap` on `side` ("source" or
    "target"). -> (new pairs, dropped pairs). A deleted segment drops its pairs; a merge leaves
    one pair per distinct (source, target)."""
    out, dropped = [], []
    for s, t in pairs:
        news = rmap.get(t if side == "target" else s)
        if not news:
            dropped.append((s, t))
            continue
        out.append((s, news[0]) if side == "target" else (news[0], t))
    return list(dict.fromkeys(out)), dropped


def serialise(m) -> dict:
    strip = lambda s: {k: (list(map(list, v)) if k == "lines" else v) for k, v in s.items()}  # noqa: E731
    ref = lambda side, i: m[side][i]["ref"]  # noqa: E731
    return {"method": m["method"], "summary": m["summary"], "counts": m["counts"], "unmapped": m["unmapped"],
            "old_segments": [strip(s) for s in m["old"]], "new_segments": [strip(s) for s in m["new"]],
            "groups": [{"kind": g["kind"], "old": [ref("old", i) for i in g["old"]],
                        "new": [ref("new", j) for j in g["new"]]} for g in m["groups"]],
            "overlaps": [{"old": ref("old", e["old"]), "new": ref("new", e["new"]), "chars": e["chars"],
                          "minor": e["minor"], "mapped": m["target"][e["old"]] == e["new"]} for e in m["edges"]],
            "rows": rows(m)}


def deserialise(d) -> dict:
    """A map saved by `serialise` -> the in-memory form (for resuming an interrupted replace)."""
    unpack = lambda s: {**s, "lines": [tuple(x) for x in s["lines"]]}  # noqa: E731
    old, new = [unpack(s) for s in d["old_segments"]], [unpack(s) for s in d["new_segments"]]
    oi = {s["ref"]: i for i, s in enumerate(old)}
    ni = {s["ref"]: j for j, s in enumerate(new)}
    edges = [{"old": oi[x["old"]], "new": ni[x["new"]], "chars": x["chars"], "minor": x["minor"]}
             for x in d["overlaps"]]
    target = [None] * len(old)
    for r in d["rows"]:
        if r["old_ref"] is not None and r["new_ref"] is not None:
            target[oi[r["old_ref"]]] = ni[r["new_ref"]]
    groups = [{"kind": g["kind"], "old": [oi[r] for r in g["old"]], "new": [ni[r] for r in g["new"]]}
              for g in d["groups"]]
    return {"method": d["method"], "summary": d["summary"], "counts": d["counts"], "unmapped": d.get("unmapped", 0),
            "old": old, "new": new, "edges": edges, "groups": groups, "target": target}
