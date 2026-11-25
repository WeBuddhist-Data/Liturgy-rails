#!/usr/bin/env python3
"""Give aligned texts (translations, commentaries) their root's segment references.

A translation aligned 1:1 to its root (translation block i <-> root block i) can still carry its
own references (`1, 2, 3…`) while the root's are `0-1, 0-2…`. This renames the translation's
references to the root's, through the live alignment — content and boundaries untouched.

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
    return {"path": path, "root": root, "eid": eid, "reid": reid, "text_id": ids.get("text_id"),
            "segs": segs, "refs": refs, "rmap": rmap, "same": refs == order}


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
    new_raw = [{"lines": s["lines"], "type": s.get("type"), "reference": p["rmap"][str(s["reference"])]}
               for s in p["segs"]]
    m = segmap.judge(content, old, content, segmap.segs(new_raw))
    if m["method"] != "renumber":
        raise ApiError(f"expected a pure renumber, the judge says {m['summary']}")
    earlier = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else None
    if earlier and earlier.get("status") != "done":
        raise ApiError(f"an unfinished map exists ({mpath.name}, status {earlier.get('status')}): resolve it first")
    ann_before = _ann(c, eid)
    doc = {"note": rel(p["path"]), "env": env, "text_id": p["text_id"], "edition_id": eid,
           "date": time.strftime("%Y-%m-%d %H:%M"), "status": "snapshot", "kind": "renumber-to-root",
           **segmap.serialise(m), "alignments_before": T._live_alignments(c, eid),
           "annotations_before": ann_before}
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    c.delete(f"/v2/editions/{eid}/segmentation")
    doc["status"] = "segmentation deleted"
    mpath.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
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
        c.put(f"/v2/editions/{a['source']}/alignments/{a['target']}",
              {"alignments": [{"source_segment_reference": s, "target_segment_reference": t} for s, t in pairs]},
              timeout=300)
        if set(T._pairs(c, a["source"], a["target"])) != set(pairs):
            raise ApiError(f"alignment {a['source']} -> {a['target']} read back differs")
        after.append({**a, "pairs": [list(x) for x in pairs]})
        dropped += [list(x) for x in lost]

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
    msg = f"{len(m['new'])} segments renamed, {len(after)} alignment(s) re-sent"
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
        p = plan_one(path, ctx)
        name = path.stem
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
        ex = ", ".join(f"{r}->{p['rmap'][r]}" for r in p["refs"][:3])
        if not a.yes:
            print(f"plan  {name}: {len(p['refs'])} refs on {p['eid']} ({ex} …)")
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
