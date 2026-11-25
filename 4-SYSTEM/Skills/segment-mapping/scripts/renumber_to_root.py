#!/usr/bin/env python3
"""Give aligned texts (translations, commentaries) their root's segment references.

A translation aligned 1:1 to its root (translation block i <-> root block i) can still carry its
own references (`1, 2, 3…`) while the root's are `0-1, 0-2…`. This renames the translation's
references to the root's, through the live alignment — content and boundaries untouched. Each
segment also takes its root segment's type, so a root retyped (a colophon made back_matter) carries
its translations along; a translation whose references already match but whose types differ is sent
too.

    python 4-SYSTEM/Skills/segment-mapping/scripts/renumber_to_root.py <notes or folders> --env <env>
           [--map | --no-map] [--yes] [--confirm prod]       # the library
    python …/renumber_to_root.py <notes or folders> --notes [--yes]        # the notes' ^block ids

Library (per note, on --env): the map translation ref -> root ref is read from the live alignment
(this edition as source, the root as target) and must be 1:1 and cover every segment, else the
note is skipped with the reason. Then, each step read back: snapshot (old segment ids,
alignments, annotations) to 4-SYSTEM/ledgers/<env>/segment-maps/<edition>.json -> DELETE
segmentation -> POST the same segments with the root's references -> new ids into the map
(with --map: reports/<env>/segment-id-maps/<note>.json + .detail.json) -> alignments re-sent
through the map -> table of contents / yigchungs / durchens / footnotes checked (re-posted
from the snapshot if the DELETE took them). Text id and edition id never change.

--notes: rewrites the note's block anchors `^old` -> `^root ref` (body blocks only; headings are
left), using the map from --env's live alignment (default prod). Only when the note's anchors
are exactly the live references (or already the root's).

Without --yes nothing is written.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "library-upload" / "scripts"))

from webuddhist import envs, segmap  # noqa: E402
from webuddhist import frontmatter as FM  # noqa: E402
from webuddhist import text as T  # noqa: E402
from webuddhist.api import ApiError, Client, items  # noqa: E402
from webuddhist.ledger import Ledger  # noqa: E402
from webuddhist.vault import FRONTMATTER_ENV, LEDGERS, VAULT, rel  # noqa: E402

ANCHOR = re.compile(r"(\s\^)([\w-]+)(\s*)$")
ANN = (("toc", "table-of-contents", "sections"), ("yigchungs", "yigchungs", None),
       ("durchens", "durchens", None), ("footnotes", "footnotes", None))


def notes(paths):
    out = []
    for p in map(Path, paths):
        files = sorted(p.rglob("*.md")) if p.is_dir() else [p]
        out += [f.resolve() for f in files if "work" not in f.parts and not f.name.startswith(("About ", "README"))]
    return out


def body_anchors(path):
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    body = text[end + 4:] if text.startswith("---") and end > 0 else text
    return [m.group(2) for l in body.splitlines() if not l.lstrip().startswith("#") and (m := ANCHOR.search(l))]


def plan_one(path, ctx):
    """-> dict with what to do, or {"skip": reason}."""
    try:
        fm = FM.read(path)
    except Exception as exc:  # noqa: BLE001
        return {"skip": f"frontmatter unreadable: {exc}"}
    root = T._root_note(fm)
    if not root or not root.is_file():
        return {"skip": "no root note (root_text / translation_of)"}
    ids, _ = T._ids_on_env(ctx, rel(path), path)
    rids, _ = T._ids_on_env(ctx, rel(root), root)
    eid, reid = ids.get("edition_id"), rids.get("edition_id")
    if not eid or not reid:
        return {"skip": f"no edition id for it or its root on {ctx.env.name}"}
    c = ctx.client
    if c.get_or_none(f"/v2/editions/{eid}") is None:
        return {"skip": f"edition {eid} not on {ctx.env.name}"}
    segs = T._segments(c, eid)
    pairs = T._pairs(c, eid, reid)
    if not pairs:
        return {"skip": "no alignment this text -> root" + (" (it is reversed)" if T._pairs(c, reid, eid) else "")}
    refs = [str(s["reference"]) for s in segs]
    fwd, back = {}, {}
    for s, t in pairs:
        fwd.setdefault(s, set()).add(t)
        back.setdefault(t, set()).add(s)
    bad = [s for s, ts in fwd.items() if len(ts) != 1] + [t for t, ss in back.items() if len(ss) != 1]
    missing = [r for r in refs if r not in fwd]
    if bad or missing:
        return {"skip": f"alignment is not 1:1 ({len(bad)} refs paired more than once, {len(missing)} segments "
                        f"unpaired, e.g. {(bad + missing)[:3]})"}
    rmap = {s: next(iter(t)) for s, t in fwd.items()}
    order = [rmap[r] for r in refs]
    if len(set(order)) != len(order):
        return {"skip": "two segments would get the same reference"}
    # Each segment takes its root segment's type too (a root colophon retyped back_matter, …).
    rtype = {str(s["reference"]): s.get("type") for s in T._segments(c, reid)}
    types = [rtype.get(rmap[r]) or s.get("type") for r, s in zip(refs, segs)]
    retyped = sum(1 for t, s in zip(types, segs) if t != s.get("type"))
    return {"path": path, "root": root, "eid": eid, "reid": reid, "text_id": ids.get("text_id"), "env": ctx.env.name,
            "segs": segs, "refs": refs, "rmap": rmap, "types": types, "retyped": retyped,
            "same": refs == order and not retyped}


def note_blocks(path):
    """The note's body blocks -> [(ref, [line text…])]: quotes (`>`), headings and blank lines are
    not translation text; a block ends at its `^ref` anchor."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    body = text[end + 4:] if text.startswith("---") and end > 0 else text
    out, cur = [], []
    for l in body.splitlines():
        if not l.strip() or l.lstrip().startswith((">", "#")):
            continue
        mm = ANCHOR.search(l)
        if mm:
            cur.append(l[:mm.start(1)])
            out.append((mm.group(2), cur))
            cur = []
        else:
            cur.append(l)
    return out


def plan_from_note(path, ctx):
    """Re-segment a translation along its note's blocks (a root split carried over): content
    untouched, the note's lines must be exactly the live lines, in order. References and types
    become the note's / root's; the alignment to the root becomes 1:1 by reference."""
    try:
        fm = FM.read(path)
    except Exception as exc:  # noqa: BLE001
        return {"skip": f"frontmatter unreadable: {exc}"}
    root = T._root_note(fm)
    if not root or not root.is_file():
        return {"skip": "no root note (root_text / translation_of)"}
    ids, _ = T._ids_on_env(ctx, rel(path), path)
    rids, _ = T._ids_on_env(ctx, rel(root), root)
    eid, reid = ids.get("edition_id"), rids.get("edition_id")
    if not eid or not reid:
        return {"skip": f"no edition id for it or its root on {ctx.env.name}"}
    c = ctx.client
    segs = T._segments(c, eid)
    content = c.get(f"/v2/editions/{eid}/content")
    live = sorted(((l["start"], l["end"]) for s in segs for l in s["lines"]))
    blocks = note_blocks(path)
    note_lines = [x.strip() for _, ls in blocks for x in ls]
    live_lines = [content[a:b].strip() for a, b in live]
    if note_lines != live_lines:
        i = next((k for k, (x, y) in enumerate(zip(note_lines, live_lines)) if x != y),
                 min(len(note_lines), len(live_lines)))
        return {"skip": f"the note's lines are not the live lines ({len(note_lines)} vs {len(live_lines)}; "
                        f"first difference at line {i + 1})"}
    rsegs = T._segments(c, reid)
    rtype = {str(s["reference"]): s.get("type") for s in rsegs}
    old_type = {}
    for s in segs:
        for l in s["lines"]:
            old_type[(l["start"], l["end"])] = s.get("type")
    new_raw, k = [], 0
    for ref, ls in blocks:
        spans = live[k:k + len(ls)]
        k += len(ls)
        new_raw.append({"lines": [{"start": a, "end": b} for a, b in spans],
                        "type": rtype.get(ref) or old_type[spans[0]], "reference": ref})
    shape = lambda ss: [(str(s["reference"]), s.get("type"), [(l["start"], l["end"]) for l in s["lines"]])  # noqa: E731
                        for s in ss]
    root_refs = [str(s["reference"]) for s in rsegs]
    want = sorted((s["reference"], s["reference"]) for s in new_raw if s["reference"] in root_refs)
    have = sorted(T._pairs(c, eid, reid))
    shape_same = shape(segs) == shape(new_raw)
    return {"path": path, "root": root, "eid": eid, "reid": reid, "text_id": ids.get("text_id"),
            "env": ctx.env.name, "segs": segs, "new_raw": new_raw, "from_note": True,
            "root_refs": root_refs, "want_pairs": want, "have_pairs": len(have),
            "align_only": shape_same and have != want, "same": shape_same and have == want}


def align_only(p, ctx):
    """Segments already follow the note: only (re)send the alignment to the root, 1:1 by reference."""
    c, eid, reid = ctx.client, p["eid"], p["reid"]
    c.put(f"/v2/editions/{eid}/alignments/{reid}",
          {"alignments": [{"source_segment_reference": s, "target_segment_reference": t} for s, t in p["want_pairs"]]},
          timeout=300)
    if sorted(T._pairs(c, eid, reid)) != p["want_pairs"]:
        raise ApiError(f"alignment {eid} -> {reid} read back differs")
    return f"segments already the note's; alignment to the root sent ({len(p['want_pairs'])} pairs, was {p['have_pairs']})"


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in ("id", "edition_id")}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _ann(c, eid):
    out = {}
    for key, path, _ in ANN:
        try:
            out[key] = items(c.get(f"/v2/editions/{eid}/{path}"))
        except ApiError as exc:
            if exc.status not in (404, 405):
                raise
            out[key] = None
    return out


def send_one(p, ctx, want_map):
    c, eid, env = ctx.client, p["eid"], ctx.env.name
    mpath = LEDGERS / env / "segment-maps" / f"{eid}.json"
    content = c.get(f"/v2/editions/{eid}/content")
    old = segmap.segs(p["segs"])
    if p.get("from_note"):
        new_raw = p["new_raw"]
    else:
        new_raw = [{"lines": s["lines"], "type": t, "reference": p["rmap"][str(s["reference"])]}
                   for s, t in zip(p["segs"], p["types"])]
    m = segmap.judge(content, old, content, segmap.segs(new_raw))
    if not p.get("from_note") and m["method"] not in ("renumber", "identical"):  # identical: types only
        raise ApiError(f"expected a pure renumber, the judge says {m['summary']}")
    earlier = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else None
    if earlier and earlier.get("status") != "done":
        raise ApiError(f"an unfinished map exists ({mpath.name}, status {earlier.get('status')}): resolve it first")
    ann_before = _ann(c, eid)
    doc = {"note": rel(p["path"]), "env": env, "text_id": p["text_id"], "edition_id": eid,
           "date": time.strftime("%Y-%m-%d %H:%M"), "status": "snapshot",
           "kind": "resegment-to-note" if p.get("from_note") else "renumber-to-root",
           **segmap.serialise(m), "alignments_before": T._live_alignments(c, eid),
           "annotations_before": ann_before}
    if p.get("from_note"):  # the alignment to the root is rebuilt 1:1 by reference, not mapped
        doc.update(align_root=p["reid"], root_refs=p["root_refs"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    c.delete(f"/v2/editions/{eid}/segmentation")
    doc["status"] = "segmentation deleted"
    mpath.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return _finish(p, ctx, want_map, doc, m, new_raw)


def resume_one(path, ctx, want_map):
    """A run stopped after the DELETE (a 502 on the POST, …): finish it from the ledger snapshot.
    -> message, or None when there is nothing to resume."""
    fm = FM.read(path)
    ids, _ = T._ids_on_env(ctx, rel(path), path)
    eid = ids.get("edition_id")
    mpath = LEDGERS / ctx.env.name / "segment-maps" / f"{eid}.json"
    if not eid or not mpath.is_file():
        return None
    doc = json.loads(mpath.read_text(encoding="utf-8"))
    if doc.get("kind") not in ("renumber-to-root", "resegment-to-note") \
            or doc.get("status") != "segmentation deleted":
        return None
    if ctx.client.get_or_none(f"/v2/editions/{eid}/segmentation/segments?limit=1&offset=0") is not None:
        raise ApiError(f"{mpath.name} says the segmentation was deleted, but {eid} has one: check by hand")
    m = segmap.deserialise(doc)
    new_raw = [{"lines": [{"start": a, "end": b} for a, b in s["lines"]], "type": s["type"], "reference": s["ref"]}
               for s in m["new"]]
    p = {"path": path, "eid": eid, "text_id": ids.get("text_id"), "env": ctx.env.name,
         "root": T._root_note(fm)}
    return "resumed: " + _finish(p, ctx, want_map, doc, m, new_raw)


def _finish(p, ctx, want_map, doc, m, new_raw):
    """From the POST on: segments, new ids, alignments, annotations, map."""
    c, eid, env = ctx.client, p["eid"], ctx.env.name
    mpath = LEDGERS / env / "segment-maps" / f"{eid}.json"
    ann_before = doc["annotations_before"]
    c.post(f"/v2/editions/{eid}/segmentation", {"segments": new_raw}, timeout=300)
    back = T._segments(c, eid)
    if [(str(s["reference"]), [(l["start"], l["end"]) for l in s["lines"]]) for s in back] != \
            [(s["reference"], [(l["start"], l["end"]) for l in s["lines"]]) for s in new_raw]:
        raise ApiError("segmentation stored, but the segments read back differ")
    if segmap.fill_new_ids(m, back) != len(m["new"]):
        raise ApiError("not every new segment matched by span")
    doc.update(segmap.serialise(m), status="segments posted")
    mpath.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    rmap = segmap.ref_map(m)
    after, dropped = [], []
    for a in doc["alignments_before"]:
        pairs, lost = segmap.remap_pairs([tuple(x) for x in a["pairs"]], rmap, a["side"])
        if doc.get("align_root") and a["source"] == eid and a["target"] == doc["align_root"]:
            pairs = [(s["reference"], s["reference"]) for s in new_raw if s["reference"] in doc["root_refs"]]
            lost = []
        c.put(f"/v2/editions/{a['source']}/alignments/{a['target']}",
              {"alignments": [{"source_segment_reference": s, "target_segment_reference": t} for s, t in pairs]},
              timeout=300)
        if set(T._pairs(c, a["source"], a["target"])) != set(pairs):
            raise ApiError(f"alignment {a['source']} -> {a['target']} read back differs")
        after.append({**a, "pairs": [list(x) for x in pairs]})
        dropped += [list(x) for x in lost]
    if doc.get("align_root") and not any(a["source"] == eid and a["target"] == doc["align_root"]
                                         for a in doc["alignments_before"]):
        # never aligned to its root before (it did not fit): align it now, 1:1 by reference
        pairs = [(s["reference"], s["reference"]) for s in new_raw if s["reference"] in doc["root_refs"]]
        c.put(f"/v2/editions/{eid}/alignments/{doc['align_root']}",
              {"alignments": [{"source_segment_reference": s, "target_segment_reference": t} for s, t in pairs]},
              timeout=300)
        if set(T._pairs(c, eid, doc["align_root"])) != set(pairs):
            raise ApiError(f"alignment {eid} -> {doc['align_root']} read back differs")
        after.append({"source": eid, "target": doc["align_root"], "side": "source", "new": True,
                      "pairs": [list(x) for x in pairs]})

    ann_after, reposted = _ann(c, eid), []
    for key, path, wrap in ANN:
        b, n = ann_before.get(key), ann_after.get(key)
        if b and not n:
            for x in b:
                body = {wrap: _strip(x.get(wrap, []))} if wrap else [_strip(x)]
                out = c.post(f"/v2/editions/{eid}/{path}", body, timeout=300)
                if key == "toc" and isinstance(out, dict) and out.get("id"):
                    reposted.append(("toc_id", x.get("id"), out["id"]))
            reposted.append((key, len(b), None))
    if any(k == "toc_id" for k, *_ in reposted) and env == FRONTMATTER_ENV:
        _, old_id, new_id = next(r for r in reposted if r[0] == "toc_id")
        FM.write_ids(p["path"], supersedes={"toc_id": {old_id}}, toc_id=new_id)
    doc.update(status="done", alignments_after=after, dropped_pairs=dropped,
               annotations_after={k: len(v or []) for k, v in _ann(c, eid).items()},
               annotations_reposted=[list(r) for r in reposted])
    mpath.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    msg = (f"{len(m['old'])} -> {len(m['new'])} segments along the note ({m['summary']})"
           if doc.get("kind") == "resegment-to-note" else f"{len(m['new'])} segments renamed")
    msg += f", {len(after)} alignment(s) re-sent"
    if reposted:
        msg += "; re-posted after the DELETE: " + ", ".join(str(r[0]) for r in reposted if r[0] != "toc_id")
    if want_map:
        idmap = T.write_segment_id_map(env, p["path"].stem, doc)
        msg += f"; map {rel(idmap)}"
    return msg


def rename_note(p, yes):
    path = p["path"]
    have = body_anchors(path)
    target = [p["rmap"][r] for r in p["refs"]]
    if have == target:
        return "already the root's ids"
    if have != p["refs"]:
        # The library may already be renamed: then the snapshot of this edition's old
        # references (the ledger map of the rename) says what the note's anchors were.
        snap = LEDGERS / p["env"] / "segment-maps" / f"{p['eid']}.json"
        doc = json.loads(snap.read_text(encoding="utf-8")) if snap.is_file() else {}
        old = [s["ref"] for s in doc.get("old_segments", [])]
        new = [s["ref"] for s in doc.get("new_segments", [])]
        if doc.get("kind") == "renumber-to-root" and doc.get("status") == "done" and old == have \
                and new == p["refs"] == target:
            pass
        else:
            return f"SKIP: the note's {len(have)} anchors are not the live references ({len(p['refs'])})"
    if not yes:
        return f"would rename {sum(a != b for a, b in zip(have, target))} anchors"
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4) + 4
    head, body = text[:end], text[end:]
    lines, k = body.split("\n"), 0
    for i, l in enumerate(lines):
        if l.lstrip().startswith("#"):
            continue
        mm = ANCHOR.search(l)
        if mm:
            lines[i] = l[:mm.start(2)] + target[k] + l[mm.end(2):]
            k += 1
    path.write_text(head + "\n".join(lines), encoding="utf-8")
    if body_anchors(path) != target:
        raise SystemExit(f"{path.name}: anchors read back differ")
    return f"{len(target)} anchors now the root's"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--env", default=None, choices=sorted(envs.ENVIRONMENTS))
    ap.add_argument("--notes", action="store_true", help="rename the notes' ^anchors instead of the library")
    ap.add_argument("--from-note", action="store_true",
                    help="re-segment along the note's blocks (a root split carried to its translations)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--map", dest="want_map", action="store_const", const=True)
    g.add_argument("--no-map", dest="want_map", action="store_const", const=False)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--confirm")
    a = ap.parse_args(argv)
    env = envs.get(a.env or ("prod" if a.notes else None) or ap.error("--env is required"))
    if not a.notes and a.yes:
        if env.protected and a.confirm != env.name:
            raise SystemExit(f"{env.name} is protected: add --confirm {env.name}")
        if a.want_map is None:
            raise SystemExit("Refused: ask the person whether the old -> new segment id map is needed, "
                             "then add --map or --no-map.")
    ctx = T.Ctx(env=env, client=Client(env), ledger=Ledger(env.name, "texts"))
    done = skipped = failed = same = 0
    for path in notes(a.paths):
        name = path.stem
        if not a.notes:
            try:
                msg = resume_one(path, ctx, a.want_map) if a.yes else None
            except ApiError as exc:
                failed += 1
                print(f"FAILED {name}: {exc}")
                break
            if msg:
                done += 1
                print(f"send  {name}: {msg}")
                continue
        p = plan_from_note(path, ctx) if a.from_note else plan_one(path, ctx)
        if "skip" in p:
            skipped += 1
            print(f"skip  {name}: {p['skip']}")
            continue
        if a.notes:
            print(f"note  {name}: {rename_note(p, a.yes)}")
            continue
        if p["same"]:
            same += 1
            continue
        if p.get("align_only"):
            if not a.yes:
                print(f"plan  {name}: segments already the note's; send the alignment to the root "
                      f"({len(p['want_pairs'])} pairs, now {p['have_pairs']})")
                done += 1
                continue
            try:
                print(f"send  {name}: {align_only(p, ctx)}")
                done += 1
            except ApiError as exc:
                failed += 1
                print(f"FAILED {name}: {exc}")
                break
            continue
        if not a.yes and p.get("from_note"):
            content = ctx.client.get(f"/v2/editions/{p['eid']}/content")
            m = segmap.judge(content, segmap.segs(p["segs"]), content, segmap.segs(p["new_raw"]))
            print(f"plan  {name}: {len(p['segs'])} -> {len(p['new_raw'])} segments on {p['eid']} "
                  f"({m['summary']}); alignment to the root 1:1 by reference")
            done += 1
            continue
        if not a.yes:
            ex = ", ".join(f"{r}->{p['rmap'][r]}" for r in p["refs"][:3])
            print(f"plan  {name}: {len(p['refs'])} refs on {p['eid']} ({ex} …)"
                  + (f", {p['retyped']} retyped to the root's type" if p["retyped"] else ""))
            done += 1
            continue
        try:
            print(f"send  {name}: {send_one(p, ctx, a.want_map)}")
            done += 1
        except ApiError as exc:
            failed += 1
            print(f"FAILED {name}: {exc}")
            break
    if not a.notes:
        print(f"\n{env.name}: {done} {'sent' if a.yes else 'planned'}, {same} already the root's, "
              f"{skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
