---
name: segment-mapping
description: >
  Judge how a text's old segments (live on a WeBuddhist library environment) map onto its new
  segments (the vault note), and build the old -> new map of segment ids and references. Decides
  the method from the data: `renumber` (only block ids changed: 1:1 by character span) or
  `resegment` (boundaries or content changed: mapped by content). Each old segment maps to at
  most one new segment. Use whenever a text is about to be re-segmented / re-uploaded keeping
  its edition id, when block ids were renumbered (`refs-renumbered`, `resegment-needed`), or
  when anyone asks "which old segment became which new one", for the old -> new segment id
  mapping, or what happens to alignments / bookmarks / transclusions when segments change.
---

# segment-mapping

Re-segmenting an edition on the library (DELETE segmentation -> POST segmentation) keeps the
**text id and edition id**, but every segment gets a **new id**, and references and
boundaries may change. Anything that pointed at an old segment may have to be moved:
alignment pairs (the server drops them with the segmentation), bookmarks or other records
holding segment ids, `![[note#^ref]]` transclusions in other repos.

This skill decides **how** old maps to new and produces the map. The judge lives in the
library-upload skill (`scripts/webuddhist/segmap.py`), so the preview here and the real
re-segmentation (`library.py <kind> replace --dev`) use exactly the same logic.

## First: ask whether the map is needed

The old -> new segment id map is not always needed. **Ask the person before re-segmenting**:
is anything outside the library pointing at these segments (bookmarks, plans, other repos'
transclusions)?

- **dev / testing**: usually not needed. For now, make it anyway (`--map`) for testing.
- **prod**: ask every time.
- `replace --yes` refuses without `--map` or `--no-map`. `--no-map` writes no report; the
  git-ignored ledger still keeps the old ids, and the alignments are still carried over (the
  library needs them either way).

## The rule: one old segment -> at most one new segment

Several new segments cannot stand in for one old one. If an old segment is split into five
new ones, it maps to **the first of them** (where its text now starts, usually keeping its
reference); the other four are **unmapped**. Several old segments may map to the same new one
(a merge).

## How the method is judged

| method | when | mapping |
|---|---|---|
| `identical` | same content, same spans, same references | nothing to map |
| `renumber` | same content, same spans, only references differ (e.g. `1` -> `0-1`) | **1:1 by span**: old segment *i* is new segment *i* |
| `resegment` | boundaries and/or content differ | **by content** (below) |

For `resegment`, character spans mean nothing across the two versions, so both contents are
read: each is tokenised (Tibetan syllables / words; spaces, tsheg, shad and other punctuation
ignored), the two token sequences are diffed, and every matched token votes for the pair
(old segment it sits in, new segment it lands in). A pair carrying at least 20 % of its old or
its new segment is an overlap; a smaller one (a boundary moved by a syllable or two) is
*minor*, shown and never used. Each old segment maps to the first new segment it overlaps.

What happened is reported per group of overlapping segments:

| kind | what happened | how it maps |
|---|---|---|
| `1:1` | one old -> one new (text may be edited inside it) | old -> new |
| `split` | one old -> several new | old -> the first part; the other parts `unmapped` |
| `merge` | several old -> one new | each old -> that new |
| `regroup` | several old <-> several new (boundaries moved) | each old -> the first new segment it overlaps; new ones nobody chose `unmapped` |
| `delete` | old text no longer in the new content | old -> none (its alignment pairs are dropped) |
| `new` | new text that was not in the old content | none -> new |

## Preview (read-only)

```
python 4-SYSTEM/Skills/segment-mapping/scripts/segment_map.py <notes or folders> --env <dev|testing|prod> \
       [--kind text|translation|commentary] [--all] [--rows] [--report]
```

- Default: every note of the kind whose segmentation differs from the environment's.
  `--all` also lists the identical ones; `--rows` prints every row (default: only rows that
  are not 1:1, plus a few examples for `renumber`).
- `--report` writes `4-SYSTEM/reports/<env>/segment-maps/preview/<note>.md` + `.csv`.
- Show the person the method and every row that is not 1:1 before anything is re-segmented.
  Name the `delete` rows (their alignment pairs will be lost) and the `unmapped` ones.

## Doing it for real

Re-segmentation is the library-upload skill's `replace` (developer-gated for linked texts):

```
python 4-SYSTEM/Skills/library-upload/scripts/library.py text replace <note> --env dev --dev                # dry run
python 4-SYSTEM/Skills/library-upload/scripts/library.py text replace <note> --env dev --dev --map --yes    # send
```

Its steps, each read back before the next:

1. **segment map + snapshot**: the map is judged and saved, with the old segment ids and every
   alignment's pairs, to `4-SYSTEM/ledgers/<env>/segment-maps/<edition_id>.json`
   **before anything is deleted**. (Git-ignored, never deleted: after the DELETE it is the
   only record of the old ids. An unfinished snapshot is kept on a re-run, not replaced.)
2. DELETE segmentation -> PATCH content (only if it differs) -> POST segmentation.
3. **new ids**: the server's new segment ids are put into the map (matched by span); with
   `--map` **the segment id map** is written to `4-SYSTEM/reports/<env>/segment-id-maps/`:
   - `<note>.json`: `{"segment": [{"old_segment_id", "new_segment_id"}]}`, one entry per old
     segment, `"deleted"` when it has no new segment. This is the deliverable other teams use.
   - `<note>.detail.json`: the same plus `edition_id`, `old_reference`, `old_type`,
     `new_reference`.
   - (Also a human-readable `4-SYSTEM/reports/<env>/segment-maps/<note>.md` + `.csv`.)
4. **alignments**: every alignment the DELETE dropped is PUT again, this edition's references
   moved through the map (one new reference per old one; a deleted segment drops its pairs,
   listed in the map). Read back and compared.
5. TOC / yigchungs / durchens / footnotes are synced from the note; the recordings' cue counts
   are compared with the snapshot.

Afterwards check the texts aligned to it: the re-sent alignment is the *live* one moved
through the map; if a translation note's own pairs differ, `push --only alignment` makes it
the note's.

## Translations and commentaries: take the root's references

A translation aligned 1:1 to its root (its block *i* renders root block *i*) should carry the
root's block ids. When the root was renumbered (`1` -> `0-1`) and the translation was not, the
library pairs them by position (`1 -> 0-1`) and nothing is broken, but the ids differ.
`renumber_to_root.py` renames the translation's references to the root's, through the live
alignment, content and boundaries untouched (method `renumber`, 1:1 by span):

```
python 4-SYSTEM/Skills/segment-mapping/scripts/renumber_to_root.py <notes or folders> --env dev                 # dry run
python 4-SYSTEM/Skills/segment-mapping/scripts/renumber_to_root.py <notes or folders> --env dev --map --yes     # library
python 4-SYSTEM/Skills/segment-mapping/scripts/renumber_to_root.py <notes or folders> --notes [--env prod] --yes  # the notes' ^anchors
```

- Library: per note, the map translation ref -> root ref comes from the live alignment (this
  edition -> root) and must be 1:1 and cover every segment, else the note is skipped with the
  reason. Snapshot (old ids, alignments, annotations) -> DELETE -> POST the same segments with
  the root's references -> new ids -> alignments re-sent -> TOC / yigchungs / durchens /
  footnotes checked (re-posted from the snapshot if gone). Same ledger map and, with `--map`,
  the same `segment-id-maps/<note>.json` as `replace`. Prod needs `--confirm prod`, and
  `--map`/`--no-map` after asking.
- `--notes`: rewrites the body anchors `^1` -> `^0-1` (headings untouched), only when the
  note's anchors are exactly the live references; run once (the notes are shared by every
  environment), with the map from prod's alignment.
- Do it only for roots whose segmentation is final: a root still `resegment-needed` changes
  again, and its translations go with it.
- These translation notes are not in the shared linter's format, so `library.py translation`
  cannot build them; this script needs no parser (it renames what is live).

## Rules

- Ask whether the map is needed before every send (`--map` / `--no-map`).
- Never re-segment without showing the person the method and the rows that are not 1:1, and
  never on prod without their explicit word (`--confirm prod`).
- One text on dev first; check the report and the aligned texts; then the rest of dev, then
  testing, then prod.
- The ledger map files are never committed and never deleted. The reports under
  `4-SYSTEM/reports/` are what other teams use to move their own records: point them there.
- A `resegment` with `delete`, `split` or `regroup` groups is a content decision: confirm with
  the person that the text change is intended before sending.
