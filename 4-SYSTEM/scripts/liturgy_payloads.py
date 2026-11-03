#!/usr/bin/env python3
"""Build WeBuddhist v2 API payloads for stamped liturgy notes.

Emits, per note in 1-SOURCES/Text/:

    <stem>.text.json      ->  POST /v2/texts
    <stem>.edition.json   ->  POST /v2/texts/{text_id}/editions

No toc.json. A flat liturgy text has no table of contents: the parser
would manufacture a one-entry TOC out of the H1 title, which is an
artifact of the parser rather than structure in the text. Texts that do
carry real `##` sections have their headings kept OUT of `content` and
`segmentation` (parser policy, 2026-09-20) but no TOC is emitted for them
either — deferred by the user on 2026-09-21; add it later with
POST /v2/editions/{id}/table-of-contents.

Content and character spans come from the vendored upstream parser
(`parser-root-text/parser.py`), which carries three documented forks of its
own — see UPSTREAM.md. Two further things are layered on top here:

1. `text.json` is built straight from the note's own frontmatter, so the
   upstream linter (dead endpoint, in-place .md patching, required
   `category_id`/`source`) is not in the path. Person `contributions` are
   NOT resolved yet and are omitted.
2. Segment types are re-derived from segment SHAPE. Upstream infers type
   from the reference string, which for this corpus always falls through
   to a document-wide default and labels every segment "verse".

Usage:
    liturgy_payloads.py --category-id <ID> --out-dir <dir> <note.md>...
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LICENSES = {
    "cc0", "public", "cc-by", "cc-by-sa", "cc-by-nd", "cc-by-nc",
    "cc-by-nc-sa", "cc-by-nc-nd", "copyrighted", "unknown",
}
EDITION_TYPES = {"diplomatic", "critical", "collated"}
# v2 API ContributorRole enum — the API rejects anything else.
CONTRIBUTOR_ROLES = {"translator", "reviser", "author", "scholar"}
ORNAMENT_RE = re.compile(r'^[༄༅༆࿓࿔\s།]+')
# A colophon names how the text came to be: composed, written, arranged,
# completed, dedicated, requested, carved.
COLOPHON_RE = re.compile(
    r'(མཛད་པ|བྲིས་པ|སྦྱར་བ|རྫོགས་ས|དགེའོ|བགྱིས་པ|ཞུས་ས|པར་དུ་བསྒྲུབས)'
)
VERSE_MAX_MEAN_LINE = 60   # chars; above this a "line" is prose, not metre


def load_upstream_parser():
    path = os.path.join(HERE, "parser-root-text", "parser.py")
    spec = importlib.util.spec_from_file_location("wb_parser", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    spec.loader.exec_module(mod)
    return mod


def classify(lines, is_last):
    """Segment type from shape.

    Tibetan liturgical verse is metrical: several short lines, each closed
    by a shad. Ritual instruction, mantra and dhāraṇī run as one long line
    with shads *inside* it. That difference is what this reads.
    """
    lengths = [len(l) for l in lines]
    mean = statistics.mean(lengths) if lengths else 0
    if is_last and mean >= VERSE_MAX_MEAN_LINE and COLOPHON_RE.search("".join(lines)):
        return "back_matter"
    if len(lines) >= 2 and mean < VERSE_MAX_MEAN_LINE:
        return "verse"
    return "paragraph"


def retype_segments(edition):
    """Replace upstream's uniform 'verse' with shape-derived types."""
    content = edition["content"]
    segs = edition["segmentation"]["segments"]
    last = len(segs) - 1
    counts = {}
    for i, seg in enumerate(segs):
        if seg["type"] == "title":
            counts["title"] = counts.get("title", 0) + 1
            continue
        lines = [content[l["start"]:l["end"]] for l in seg["lines"]]
        seg["type"] = classify(lines, i == last)
        counts[seg["type"]] = counts.get(seg["type"], 0) + 1
    return counts


def build_text(fm, category_id, path):
    lang = (fm.get("lang_tag") or "").strip()
    if not lang:
        raise ValueError("lang_tag missing — cannot build a localized title")

    title = (fm.get("title") or "").strip()
    if not title:
        raise ValueError("title missing")
    # The API's title field carries the bare title; the ༄༅། །  ornament
    # belongs to the page, not the name.
    title = ORNAMENT_RE.sub("", title).strip()
    if not title:
        raise ValueError("title is only ornament")

    out = {
        "title": {lang: title},
        "language": lang,
        "category_id": category_id,
    }

    lic = (fm.get("license") or "").strip()
    if lic:
        if lic not in LICENSES:
            raise ValueError(f"license {lic!r} not one of {sorted(LICENSES)}")
        out["license"] = lic

    bdrc = (fm.get("bdrc_work_id") or fm.get("bdrc") or "").strip()
    if bdrc:
        out["bdrc"] = bdrc

    date = (fm.get("date") or "").strip()
    if date:
        out["date"] = date

    # FORK(liturgy-rails): contributions ARE emitted now. Resolving an author
    # to a backend person id was the "separate step" this comment used to defer
    # to; it has been done (1-SOURCES/liturgy-persons.json), and
    # stamp_metadata.py --authors writes the ids back onto the note's `author:`
    # field as bracket tags:
    #
    #     author: གླང་རི་ཐང་པ། [person:u4IF…] [bdrc:P3445] [role:author]
    #
    # Shape required by the v2 API (PersonContributionInput): `type` and `role`
    # are REQUIRED, role must be one of ContributorRole. An author with no
    # [person:…] tag is STILL dropped rather than guessed — that was the right
    # call and it stands.
    contributions = []
    for part in (fm.get("author") or "").split(";"):
        part = part.strip()
        if not part:
            continue
        pid = re.search(r"\[person:([^\]]+)\]", part)
        if not pid:
            continue                      # unresolved author: drop, never guess
        bid = re.search(r"\[bdrc:([^\]]+)\]", part)
        rol = re.search(r"\[role:([^\]]+)\]", part)
        role = (rol.group(1).strip() if rol else "author")
        if role not in CONTRIBUTOR_ROLES:
            raise ValueError(f"role {role!r} not one of {sorted(CONTRIBUTOR_ROLES)}")
        # The API rejects a person contribution carrying BOTH: "Only one of id
        # or bdrc_id can be provided". The backend's own person id is the more
        # precise key, so it wins; bdrc_id is only a fallback for a person we
        # could identify in BDRC but not resolve in the library.
        entry = {"type": "person", "role": role, "id": pid.group(1).strip()}
        contributions.append(entry)
    if contributions:
        out["contributions"] = contributions

    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="stamped .md notes")
    ap.add_argument("--category-id", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    P = load_upstream_parser()
    os.makedirs(args.out_dir, exist_ok=True)
    ok = bad = 0

    for path in args.paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            fm, _ = P._read_source(__import__("pathlib").Path(path))
            text = build_text(fm, args.category_id, path)

            etype = (fm.get("edition_type") or "critical").strip()
            if etype not in EDITION_TYPES:
                raise ValueError(f"edition_type {etype!r} not one of {sorted(EDITION_TYPES)}")

            # upstream writes its own output/ copy; we only want the dict
            _, edition = P.build_edition(__import__("pathlib").Path(path), None)
            # `_headings` is TOC input the parser hands back in memory; it is
            # not edition data and the API must never see it.
            edition.pop("_headings", None)
            counts = retype_segments(edition)

            meta = {"type": etype}
            src = (fm.get("source") or "").strip()
            if src:
                meta["source"] = src
            edition["metadata"] = meta

            for name, payload in ((f"{stem}.text.json", text),
                                  (f"{stem}.edition.json", edition)):
                with open(os.path.join(args.out_dir, name), "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
            ok += 1
            n = len(edition["segmentation"]["segments"])
            spans = sum(len(s["lines"]) for s in edition["segmentation"]["segments"])
            print(f"ok     {stem}")
            print(f"         {len(edition['content'])} chars | {n} segments | "
                  f"{spans} line spans | {counts}")
        except Exception as exc:
            print(f"ABORT  {stem}\n         {type(exc).__name__}: {exc}")
            bad += 1

    print(f"\n{ok} built into {args.out_dir}, {bad} aborted")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
