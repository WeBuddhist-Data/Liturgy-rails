#!/usr/bin/env python3
"""Fill an audio note's Mapping table from its oTranscribe (.otr) timestamps.

usage: otr_to_mapping.py [NOTE_OR_FOLDER ...] [--write] [--live] [--detail]

For every audio note (default: all of 1-SOURCES/Audio) whose text folder has
an `otr/*.otr` file:

1. The .otr text and the root text's blocks are both normalised (tsheg, shad,
   spaces dropped) and aligned character by character (difflib). Each .otr
   timestamp lands at a position in the root text.
2. A timestamp within 3 characters of a block's start starts that block.
   Timestamps that land inside a block (a refrain sung twice, a line split in
   two) are reported, not used: a cue covers a whole block.
3. Each started block gets Start = its timestamp and End = the next started
   block's Start (the last one ends with the recording).
4. The Mapping table is rebuilt: `#` = row number from 0, Section = the root
   text's block id (`^id`), Text = the row's existing text, Start/End = the
   times. Existing rows are kept (matched to blocks by Section id, else by
   their Text), untimed ones stay empty, and a timed block with no row gets
   one after its predecessor.
5. The rebuilt note is run through linter-audio before anything is written; a
   note with a Mapping/cue error is not written. With --live the linter also
   checks every timed block against the live edition on dev, which is what
   the cues will be placed in.

Without --write nothing changes: it prints what would. Line endings are kept.
Then upload the cues with `library.py audio push --env dev --only cues <text>` (skill library-upload) (recording already uploaded).
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "linter-audio"))
import audio_note as an  # noqa: E402
import lint_audio as la  # noqa: E402

TOL = 3                      # characters: a stamp this close to a block start starts that block
STAMP_RE = re.compile(r'<span class="timestamp" data-timestamp="([\d.]+)">[^<]*</span>')


def fmt(ms):
    m, s = divmod(ms / 1000, 60)
    return f"{int(m)}:{s:06.3f}"


def otr_stream(path: Path):
    """-> (otr json, normalised text, [(ms, offset in that text)])"""
    d = json.loads(path.read_text(encoding="utf-8-sig"))
    parts = STAMP_RE.split(d.get("text", ""))

    def clean(s):
        s = re.sub(r"<br\s*/?>|</p>", "\n", s)
        return la.norm(html.unescape(re.sub(r"<[^>]+>", "", s)))
    stream, stamps = clean(parts[0]), []
    for i in range(1, len(parts), 2):
        stamps.append((round(float(parts[i]) * 1000), len(stream)))
        stream += clean(parts[i + 1])
    return d, stream, stamps


def align(n: an.AudioNote, otr: Path, dur_ms: int):
    """-> dict with times {block id: (start_ms, end_ms)} and a report."""
    d, ostream, stamps = otr_stream(otr)
    ids = list(n.root_blocks)
    starts, estream = [], ""
    for bid in ids:
        starts.append(len(estream))
        estream += la.norm(n.root_blocks[bid]["text"])
    ends = starts[1:] + [len(estream)]

    sm = difflib.SequenceMatcher(None, ostream, estream, autojunk=False)
    blocks = sm.get_matching_blocks()

    def to_edition(o):
        for a, b, size in blocks:
            if a <= o < a + size:
                return b + (o - a)
            if a > o:
                return b                     # in a gap: snap to the next match
        return len(estream)

    started, inside, clash = {}, [], []
    for ms, o in stamps:
        e = to_edition(o)
        k = max(bisect.bisect_right(starts, e) - 1, 0)
        if k + 1 < len(ids) and starts[k + 1] - e <= TOL:
            k += 1
        if abs(e - starts[k]) <= TOL:
            if ids[k] in started:
                clash.append((ms, ids[k]))
            else:
                started[ids[k]] = ms
        else:
            inside.append((ms, ids[k], e - starts[k], ends[k] - starts[k]))

    times, order = {}, [bid for bid in ids if bid in started]
    for i, bid in enumerate(order):
        times[bid] = (started[bid], started[order[i + 1]] if i + 1 < len(order) else dur_ms)

    diffs = [(ids[max(bisect.bisect_right(starts, j1) - 1, 0)], op, ostream[i1:i2][:20], estream[j1:j2][:20])
             for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    spoken = [b for b in ids if not n.root_blocks[b]["heading"] and b != "0"]
    return {
        "times": times, "inside": inside, "clash": clash, "diffs": diffs,
        "similarity": sm.ratio(), "stamps": len(stamps),
        "unstamped": [b for b in spoken if b not in times],
        "media": d.get("media"),
    }


def mapping_span(lines):
    """-> (heading index, first table line, end index exclusive) in the note's lines, or None."""
    head = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Mapping\s*$", l)), None)
    if head is None:
        return None
    i = head + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        if lines[i].startswith("#"):
            return None
        i += 1
    if i == len(lines):
        return None
    j = i
    while j < len(lines) and lines[j].lstrip().startswith("|"):
        j += 1
    return head, i, j


def rebuild(n: an.AudioNote, text: str, otr: Path, res, dur_ms):
    """-> (new note text, rows changed, rows added, problems)"""
    lines = text.split("\n")
    span = mapping_span(lines)
    if span is None:
        return None, 0, 0, ["no `## Mapping` heading followed by a table"]
    head, t0, t1 = span
    ids = list(n.root_blocks)
    old_rows = n.mapping or []
    rows, problems, pos, used = [], [], 0, set()
    for r in old_rows:
        sec = r.section.strip()
        bid = sec[1:] if sec.startswith("^") and sec[1:] in n.root_blocks else None
        if bid is None:
            first = la.norm(r.text.replace("…", "").replace("...", ""))[:15]
            bid = next((ids[i] for i in range(pos, len(ids))
                        if first and la.norm(n.root_blocks[ids[i]]["text"]).startswith(first)), None)
        if bid is None:
            problems.append(f"Mapping line {r.line}: row {r.number!r} {r.text[:25]!r} matches no block")
            continue
        pos = ids.index(bid) + 1
        used.add(bid)
        rows.append([bid, r.text, r.start, r.end])
    for bid in [b for b in ids if b in res["times"] and b not in used]:
        k = ids.index(bid)
        prev = next((ids[j] for j in range(k - 1, -1, -1) if ids[j] in used), None)
        at = next((i + 1 for i, r in enumerate(rows) if r[0] == prev), 0) if prev else 0
        rows.insert(at, [bid, n.root_blocks[bid]["first"], "", ""])
        used.add(bid)
    changed = added = 0
    out = []
    for i, (bid, txt, st0, en0) in enumerate(rows):
        st, en = res["times"].get(bid, (None, None))
        st, en = (fmt(st), fmt(en)) if st is not None else ("", "")
        if not txt:
            txt = n.root_blocks[bid]["first"]
        changed += (st, en) != (st0, en0)
        out.append(f"| {i} | ^{bid} | {txt} | {st} | {en} |")
    added = len(rows) - len(old_rows) + len(problems)

    intro = " ".join(l.strip() for l in lines[head + 1:t0] if l.strip())
    m = re.match(r"(Total duration [^.]*\.\d+\.|Total duration [^.]*\.)", intro)
    total = m.group(1) if m else f"Total duration {fmt(dur_ms)}."
    new_intro = (f"{total} Start/End (m:ss.sss) come from `otr/{otr.name}`: each Start is a timestamp "
                 "there, each End is the next block's Start (the last ends with the recording). "
                 "Section is the root text's block id.")
    table = ["| #   | Section | Text (first line) | Start | End |",
             "| --- | ------- | ----------------- | ----- | --- |"] + out
    new_lines = lines[:head + 1] + ["", new_intro, ""] + table + lines[t1:]
    return "\n".join(new_lines), changed, added, problems


def process(path: Path, args):
    n = an.parse_note(path)
    rel = path.relative_to(an.VAULT)
    otrs = sorted((n.text_folder / "otr").glob("*.otr")) if (n.text_folder / "otr").is_dir() else []
    if not otrs:
        return None
    print(f"\n=== {rel}")
    if len(otrs) > 1:
        print(f"    SKIP several .otr files: {[o.name for o in otrs]}")
        return False
    otr = otrs[0]
    if not n.root_note:
        print(f"    SKIP root_text `[[{n.root_link}]]` does not exist")
        return False
    if not n.root_blocks:
        print(f"    SKIP {n.root_note.name} cannot be read, or has no block ids")
        return False
    durs = [r.duration_seconds for r in n.recordings if isinstance(r.duration_seconds, (int, float))]
    if len(n.recordings) != 1 or len(durs) != 1:
        print("    SKIP the note needs exactly one recording with duration_seconds")
        return False
    dur_ms = round(durs[0] * 1000)

    res = align(n, otr, dur_ms)
    print(f"    {otr.name}: {res['stamps']} timestamps, {len(res['times'])} block starts, "
          f".otr ~ root text similarity {res['similarity']:.3f}")
    rec_name = Path(str(n.recordings[0].declared or "")).name
    if res["media"] and rec_name and Path(res["media"]).stem.strip() != Path(rec_name).stem.strip():
        print(f"    W .otr was transcribed against {res['media']!r}, the note's file is {rec_name!r}")
    if res["clash"]:
        print(f"    W {len(res['clash'])} timestamp(s) land on a block already started: "
              + ", ".join(f"{fmt(ms)} at ^{b}" for ms, b in res["clash"][:5]))
    if res["inside"]:
        print(f"    - {len(res['inside'])} timestamp(s) inside a block (not a cue boundary): "
              + ", ".join(f"{fmt(ms)} in ^{b} ({o}/{ln} chars)" for ms, b, o, ln in res["inside"][:5])
              + (" …" if len(res["inside"]) > 5 else ""))
    if res["unstamped"]:
        print(f"    - spoken blocks with no timestamp: {', '.join('^' + b for b in res['unstamped'])}")
    if res["diffs"]:
        print(f"    - {len(res['diffs'])} text difference(s) between the .otr and the root text"
              + ("" if args.detail else " (--detail lists them)"))
        if args.detail:
            for bid, op, a, b in res["diffs"]:
                print(f"        ^{bid:7} {op:7} otr {a!r}  root {b!r}")

    raw = path.read_bytes().decode("utf-8-sig")
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    new_text, changed, added, problems = rebuild(n, text, otr, res, dur_ms)
    for p in problems:
        print(f"    E {p}")
    if new_text is None or problems:
        print("    not written")
        return False

    check = la.lint(an.parse_note(path, new_text), live_host="dev" if args.live else None)
    bad = [m for m in check["errors"] if m["code"].startswith(("mapping", "cue", "live"))]
    for m in bad:
        print(f"    E [{m['code']}] {m['message']}")
    for m in check["warnings"]:
        if m["code"].startswith(("mapping", "cue")):
            print(f"    W [{m['code']}] {m['message']}")
    if bad:
        print("    not written: the rebuilt table fails the linter")
        return False

    if new_text == text:
        print("    up to date")
        return True
    print(f"    {changed} row(s) with new times, {added} row(s) added"
          + ("" if args.live else "; run with --live to check the cues against the live edition"))
    if args.write:
        path.write_bytes(new_text.replace("\n", nl).encode("utf-8"))
        print("    WROTE")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="notes or folders (default: 1-SOURCES/Audio)")
    ap.add_argument("--write", action="store_true", help="write the rebuilt tables")
    ap.add_argument("--live", action="store_true", help="check timed blocks against the live edition on dev")
    ap.add_argument("--detail", action="store_true", help="list every .otr / root-text difference")
    args = ap.parse_args(argv)
    results = [process(p, args) for p in la.collect(args.paths)]
    results = [r for r in results if r is not None]
    print(f"\n{len(results)} note(s) with an .otr; {sum(1 for r in results if not r)} not written/skipped"
          + ("" if args.write else " (dry run: pass --write to write)"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
