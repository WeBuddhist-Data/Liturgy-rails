#!/usr/bin/env python3
"""Judge how a note's segmentation maps onto what an environment holds, and show the map.

Read-only: nothing is sent. The same judge (library-upload's webuddhist/segmap.py) is what
`library.py <kind> replace --dev` uses when it re-segments for real.

    python 4-SYSTEM/Skills/segment-mapping/scripts/segment_map.py <notes or folders> --env dev
           [--kind text|translation|commentary] [--all] [--rows] [--report]

  (no flag)  every named note whose segmentation differs from the environment's
  --all      also the notes whose segmentation is identical
  --rows     print every old -> new row (default: only the rows that are not 1:1)
  --report   write 4-SYSTEM/reports/<env>/segment-maps/preview/<note>.md + .csv

The method is judged per note:
  identical   same content, spans and references: nothing to map
  renumber    same content and spans, references differ: 1:1 by span
  resegment   boundaries and/or content differ: mapped by content
              (1:1, 1:many, many:1, many:many, delete, new)
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "library-upload" / "scripts"))

from webuddhist import envs, segmap  # noqa: E402
from webuddhist import text as T  # noqa: E402
from webuddhist.api import Client  # noqa: E402
from webuddhist.ledger import Ledger  # noqa: E402
from webuddhist.vault import REPORTS, rel  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="notes or folders (default: every note of the kind)")
    ap.add_argument("--env", required=True, choices=sorted(envs.ENVIRONMENTS))
    ap.add_argument("--kind", default="text", choices=["text", "translation", "commentary"])
    ap.add_argument("--all", action="store_true", help="also show notes whose segmentation is identical")
    ap.add_argument("--rows", action="store_true", help="print every row, not only the ones that are not 1:1")
    ap.add_argument("--report", action="store_true", help="write a preview report per note")
    a = ap.parse_args(argv)

    env = envs.get(a.env)
    ctx = T.Ctx(env=env, client=Client(env), ledger=Ledger(env.name, "texts"))
    states = T.assess_all(a.kind, a.paths, ctx)
    totals = {}
    for it in sorted(states, key=lambda s: s.title):
        if not it.live_segs or not it.p.get("edition"):
            continue
        m = T.segment_map(it)
        totals[m["method"]] = totals.get(m["method"], 0) + 1
        if m["method"] == "identical" and not a.all:
            continue
        print(f"\n{it.title}\n  {m['summary']}")
        for r in segmap.rows(m):
            if a.rows or r["kind"] != "1:1":
                print(f"    {r['kind']:10} {str(r['old_ref'] or '—'):>8} ({r['old_id'] or '—'})  ->  "
                      f"{str(r['new_ref'] or '—'):<8} {r['chars']:>5} chars")
        if m["method"] == "renumber" and not a.rows:
            ex = [(r["old_ref"], r["new_ref"]) for r in segmap.rows(m) if r["old_ref"] != r["new_ref"]][:4]
            print("    e.g. " + ", ".join(f"{x} -> {y}" for x, y in ex))
        minor = [e for e in m["edges"] if e["minor"]]
        if minor:
            print("    minor overlaps (not used): " + ", ".join(
                f"{m['old'][e['old']]['ref']}->{m['new'][e['new']]['ref']} ({e['chars']})" for e in minor[:8]))
        if a.report:
            folder = REPORTS / env.name / "segment-maps" / "preview"
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / f"{it.path.stem}.csv", "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["kind", "old_ref", "old_segment_id", "new_ref", "chars"])
                for r in segmap.rows(m):
                    w.writerow([r["kind"], r["old_ref"] or "", r["old_id"] or "", r["new_ref"] or "", r["chars"]])
            lines = [f"# Segment map preview: {it.path.stem}", "",
                     f"- environment `{env.name}`, edition `{it.edition_id}`", f"- {m['summary']}", "",
                     "| kind | old ref | old segment id | new ref | chars |", "|---|---|---|---|---|"]
            lines += [f"| {r['kind']} | {r['old_ref'] or '—'} | {r['old_id'] or '—'} | {r['new_ref'] or '—'} | "
                      f"{r['chars']} |" for r in segmap.rows(m)]
            (folder / f"{it.path.stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"    report: {rel(folder / (it.path.stem + '.md'))}")
    print("\n" + ", ".join(f"{v} {k}" for k, v in sorted(totals.items())) + f" on {env.name}")


if __name__ == "__main__":
    main()
