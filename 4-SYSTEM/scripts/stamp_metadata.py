#!/usr/bin/env python3
"""Stamp backend ids, track metadata and researched titles into the vault.

Four independent, idempotent passes. Run from the vault root.

    python3 4-SYSTEM/scripts/stamp_metadata.py --ids
    python3 4-SYSTEM/scripts/stamp_metadata.py --track-meta
    python3 4-SYSTEM/scripts/stamp_metadata.py --titles          # needs the registry
    python3 4-SYSTEM/scripts/stamp_metadata.py --source-titles   # needs the registry
    python3 4-SYSTEM/scripts/stamp_metadata.py --all

RE-RUN THIS AFTER EVERY dharmamitra-translate RUN. That script rebuilds each
translation file's frontmatter and body from its ledger on every invocation and
knows nothing about backend ids, provenance keys or researched titles, so it
silently reverts all of them. This script restores them and is safe to repeat.

--ids
    Copies ``text_id`` and ``edition_id`` out of
    ``4-SYSTEM/scripts/upload_ledger.json`` (the WeBuddhist library upload
    receipt) into the frontmatter of every ``1-SOURCES/Text/*.md`` note and of
    the matching translation file in every track under
    ``3-TRANSFORMATIONS/Translations/``. The notes previously carried no ids —
    they lived only in the ledger — which meant a translation could not be
    mapped back to the text it belongs to at upload time.

--track-meta
    Fills the four provenance keys on every *translation* file:
    ``author`` (empty — machine output has no author), ``license``,
    ``source`` (the generator's website) and ``bdrc_work_id`` (empty).

--titles
    Reads ``1-SOURCES/liturgy-titles.json`` and writes the researched title of
    each text into its translation files: the frontmatter ``title``, and the H1,
    which becomes the target-language title with the Tibetan kept as a quoted
    line beneath it. Any machine-translated rendering of the title block itself
    is replaced by the researched title.

--source-titles
    Writes ``title_en`` / ``title_zh`` onto the source notes, so a text's names
    live with the text.

--tags
    Reads every text's live ``tag_ids`` from the WeBuddhist library (one
    paginated listing, ~8 calls) and writes two keys on every source note and
    translation file: ``tags`` (registry keys, e.g. ``[practice/chant]`` —
    Obsidian nested-tag syntax) and ``tag_ids`` (the backend ids). A
    translation file is looked up by its own ``text_id`` when it has one,
    else by ``translation_of_text_id``, because the backend cascades a root
    text's tags to its translations. Ids not in
    ``4-SYSTEM/tag-registry.json`` are kept as ids and reported.

Nothing here writes translation *content*. --ids and --source-titles are the
only passes that touch ``1-SOURCES/``.
"""

import argparse
import json
import os
import re
import sys

VAULT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(VAULT, "1-SOURCES", "Text")
TRACKS_DIR = os.path.join(VAULT, "3-TRANSFORMATIONS", "Translations")
LEDGER = os.path.join(VAULT, "4-SYSTEM", "scripts", "upload_ledger.json")
REGISTRY = os.path.join(VAULT, "1-SOURCES", "liturgy-titles.json")
PERSONS = os.path.join(VAULT, "1-SOURCES", "liturgy-persons.json")
TAG_REGISTRY = os.path.join(VAULT, "4-SYSTEM", "tag-registry.json")
API_BASE = "https://library.webuddhist.com"

# Files in a track folder that are track documentation, not a translated text.
TRACK_DOCS = {"about.md", "style.md", "context-header.md", "requirements.md", "termbase.md"}

# `generator:` prefix -> the generator's website, written to `source:` (the
# edition's provenance URL on the backend). A translation file names its own
# generator; the site is derived from that, never assumed track-wide.
GENERATOR_SITES = {
    "dharmamitra": "https://dharmamitra.org",
    "gemini": "https://ai.google.dev",
}
TRACK_LABELS = {
    "dharmamitra": "DharmaMitra zero-shot",
    "gemini": "Gemini zero-shot",
}
TRACK_LICENSE = "public"          # LicenseType enum; "public domain" is rejected by the v2 API


# --------------------------------------------------------------------------
# frontmatter helpers — line-based, so every byte we do not touch is preserved
# --------------------------------------------------------------------------

def split_note(text):
    """-> (pre, fm_lines, body). Raises if there is no frontmatter block."""
    if not text.startswith("---\n"):
        raise ValueError("no frontmatter")
    end = text.index("\n---\n", 3)
    fm = text[4:end + 1].split("\n")[:-1]
    return "---\n", fm, text[end + 5:]


def join_note(pre, fm_lines, body):
    return pre + "\n".join(fm_lines) + "\n---\n" + body


def key_of(line):
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", line)
    return m.group(1) if m else None


def find_key(fm_lines, key):
    for i, line in enumerate(fm_lines):
        if key_of(line) == key:
            return i
    return -1


def yaml_value(value):
    """Quote only when the value would otherwise not survive a YAML round-trip."""
    if value == "":
        return ""
    if re.search(r'^[\s>|&*!%@`\'"\[{-]|[:#]\s|["\']$|\s$', value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def drop_key(fm_lines, key):
    """Remove a key entirely. Returns True if something was removed."""
    i = find_key(fm_lines, key)
    if i < 0:
        return False
    del fm_lines[i]
    return True


def set_key(fm_lines, key, value, after=None):
    """Set key=value. Replaces in place if present, else inserts after `after`
    (a key name or list of them, first match wins) or appends."""
    rendered = f"{key}:" + (f" {yaml_value(value)}" if value != "" else "")
    i = find_key(fm_lines, key)
    if i >= 0:
        if fm_lines[i] != rendered:
            fm_lines[i] = rendered
            return True
        return False
    pos = len(fm_lines)
    if after:
        for anchor in ([after] if isinstance(after, str) else after):
            j = find_key(fm_lines, anchor)
            if j >= 0:
                pos = j + 1
                break
    fm_lines.insert(pos, rendered)
    return True


def get_key(fm_lines, key):
    """The (unquoted) value of `key`, or "" when absent."""
    i = find_key(fm_lines, key)
    if i < 0:
        return ""
    return fm_lines[i].split(":", 1)[1].strip().strip('"').strip("'")


def set_list(fm_lines, key, items, after=None):
    """Set key to a YAML flow list of plain scalars, or drop the key when
    `items` is empty. Same placement rules as set_key."""
    if not items:
        return drop_key(fm_lines, key)
    rendered = f"{key}: [" + ", ".join(items) + "]"
    i = find_key(fm_lines, key)
    if i >= 0:
        if fm_lines[i] != rendered:
            fm_lines[i] = rendered
            return True
        return False
    pos = len(fm_lines)
    if after:
        for anchor in ([after] if isinstance(after, str) else after):
            j = find_key(fm_lines, anchor)
            if j >= 0:
                pos = j + 1
                break
    fm_lines.insert(pos, rendered)
    return True


def _generator_kind(fm_lines):
    g = get_key(fm_lines, "generator").lower()
    for prefix in GENERATOR_SITES:
        if g.startswith(prefix):
            return prefix
    return ""


# --------------------------------------------------------------------------

def load_ledger():
    with open(LEDGER) as fh:
        return json.load(fh)


ONLY_TRACK = None   # set by --track: restrict every pass to one track folder


def track_files():
    """-> [(stem, path)] for every translated-text file in every track.

    `--track` narrows this to one folder, which matters when a translation run
    is in flight: stamping a track the runner is actively rewriting races it."""
    out = []
    for root, _dirs, files in os.walk(ONLY_TRACK or TRACKS_DIR):
        for name in sorted(files):
            if not name.endswith(".md") or name in TRACK_DOCS:
                continue
            path = os.path.join(root, name)
            with open(path) as fh:
                head = fh.read(4096)
            m = re.search(r"^translation_of:\s*1-SOURCES/Text/(.+?)\.md\s*$", head, re.M)
            if m:
                out.append((m.group(1), path))
    return out


def edit(path, fn):
    with open(path) as fh:
        text = fh.read()
    pre, fm, body = split_note(text)
    changed = fn(fm, body)
    if isinstance(changed, tuple):
        changed, body = changed
    if changed:
        with open(path, "w") as fh:
            fh.write(join_note(pre, fm, body))
    return changed


# --------------------------------------------------------------------------
# pass 1 — backend ids
# --------------------------------------------------------------------------

def pass_ids():
    ledger = load_ledger()
    n_src = n_tr = 0
    missing = []

    for name in sorted(os.listdir(SRC_DIR)):
        if not name.endswith(".md"):
            continue
        stem = name[:-3]
        rec = ledger.get(stem)
        if not rec:
            missing.append(("source", stem))
            continue

        def apply(fm, body, rec=rec):
            a = set_key(fm, "text_id", rec["text_id"], after=["category_id", "edition_type", "file_type"])
            b = set_key(fm, "edition_id", rec["edition_id"], after="text_id")
            return a or b

        n_src += bool(edit(os.path.join(SRC_DIR, name), apply))

    for stem, path in track_files():
        rec = ledger.get(stem)
        if not rec:
            missing.append(("track", stem))
            continue

        def apply(fm, body, rec=rec):
            # WEMI: a translation is its own Expression, so it becomes its own
            # text with its own edition — not another edition of the Tibetan
            # text. One rule holds everywhere in the vault:
            #
            #   text_id / edition_id            THIS document's own ids
            #   translation_of_text_id / _edition_id   what it points at
            #
            # So a translation carries the Tibetan ids as pointers, and its own
            # ids stay EMPTY until it is uploaded and the backend assigns them.
            # (Writing the Tibetan ids into the bare keys, as an earlier version
            # of this script did, claimed the translation *was* the Tibetan text
            # and edition.)
            changed = set_key(fm, "translation_of_text_id", rec["text_id"],
                              after="translation_of")
            changed |= set_key(fm, "translation_of_edition_id", rec["edition_id"],
                               after="translation_of_text_id")
            # own ids: create them empty, never overwrite one already assigned
            if find_key(fm, "text_id") < 0:
                changed |= set_key(fm, "text_id", "", after="translation_of_edition_id")
            if find_key(fm, "edition_id") < 0:
                changed |= set_key(fm, "edition_id", "", after="text_id")
            return changed

        n_tr += bool(edit(path, apply))

    print(f"ids: {n_src} source notes, {n_tr} translation files updated")
    for kind, stem in missing:
        print(f"  !! no ledger entry for {kind}: {stem}", file=sys.stderr)
    return len(missing)


# --------------------------------------------------------------------------
# pass 2 — translation-track provenance metadata
# --------------------------------------------------------------------------

def pass_track_meta():
    n = 0
    for _stem, path in track_files():
        def apply(fm, body):
            changed = False
            changed |= set_key(fm, "author", "", after=["lang_tag", "target_language"])
            changed |= set_key(fm, "license", TRACK_LICENSE, after="author")
            kind = _generator_kind(fm)
            site = GENERATOR_SITES.get(kind) or get_key(fm, "source")
            changed |= set_key(fm, "source", site, after="license")
            changed |= set_key(fm, "bdrc_work_id", "", after="source")
            return changed
        n += bool(edit(path, apply))
    print(f"track-meta: {n} translation files updated")
    return 0


# --------------------------------------------------------------------------
# pass 3 — researched titles
# --------------------------------------------------------------------------

def _title_key(lang_tag):
    """`<tag>_title` in the registry — en_title, zh_title, hi_title, …"""
    return f"{lang_tag}_title" if lang_tag else None


def pass_titles():
    if not os.path.exists(REGISTRY):
        print(f"titles: {REGISTRY} not found — run the research pass first", file=sys.stderr)
        return 1
    with open(REGISTRY) as fh:
        reg = {r["stem"]: r for r in json.load(fh)["texts"]}

    n = 0
    skipped = []
    for stem, path in track_files():
        rec = reg.get(stem)
        if not rec:
            skipped.append((stem, "no registry entry"))
            continue
        with open(path) as fh:
            text = fh.read()
        tag = re.search(r"^lang_tag:\s*(\S+)\s*$", text, re.M)
        lang = re.search(r"^target_language:\s*(.+?)\s*$", text, re.M)
        tkey = _title_key(tag.group(1)) if tag else None
        if not tkey or not rec.get(tkey):
            skipped.append((stem, "no researched title for that language"))
            continue

        title = rec[tkey]
        language = lang.group(1).strip() if lang else ""
        attested = rec.get(tkey.replace("_title", "_attested"), False)
        src_url = rec.get(tkey.replace("_title", "_source"), "") or ""

        def apply(fm, body, title=title, language=language, attested=attested,
                  src_url=src_url, rec=rec):
            changed = False
            label = TRACK_LABELS.get(_generator_kind(fm), "machine zero-shot")
            display = f"{title} — {label} ({language})" if language else title
            changed |= set_key(fm, "title", display)
            # The researched title, on its own, plus where it came from.
            changed |= set_key(fm, "title_translated", title, after="title")
            changed |= set_key(fm, "title_original", rec["bo_title"], after="title_translated")
            changed |= set_key(fm, "title_attested",
                               "true" if attested else "false", after="title_original")
            changed |= set_key(fm, "title_source", src_url, after="title_attested")
            new_body, n_body = _retitle_body(body, title, rec.get("bo_heading", ""))
            return (changed or n_body > 0), new_body

        n += bool(edit(path, apply))

    print(f"titles: {n} translation files updated")
    for stem, why in skipped:
        print(f"  skipped {stem}: {why}")
    return 0


def pass_source_titles():
    """Put the researched titles on the source notes themselves.

    The keys are `title_en` / `title_zh` and NOT `title_in_english`: the
    dharmamitra-translate script reads `title_in_english` to build its output
    filename and its context header, so writing that key would silently rename
    every translation file on the next run.
    """
    if not os.path.exists(REGISTRY):
        print(f"source-titles: {REGISTRY} not found — build the registry first", file=sys.stderr)
        return 1
    with open(REGISTRY) as fh:
        reg = {r["stem"]: r for r in json.load(fh)["texts"]}

    n = 0
    for name in sorted(os.listdir(SRC_DIR)):
        if not name.endswith(".md"):
            continue
        rec = reg.get(name[:-3])
        if not rec:
            continue

        def apply(fm, body, rec=rec):
            # Every `<tag>_title` the registry holds becomes `title_<tag>` on
            # the note, each inserted after the previous one so the block of
            # titles stays together directly under `title`.
            changed = False
            anchors = ["title"]
            for key in rec:
                m = re.fullmatch(r"([a-z]{2,3})_title", key)
                if not m or not rec.get(key) or m.group(1) == "bo":
                    continue        # bo_title IS the note's own title
                note_key = f"title_{m.group(1)}"
                changed |= set_key(fm, note_key, rec[key], after=list(reversed(anchors)))
                anchors.append(note_key)
            return changed

        n += bool(edit(os.path.join(SRC_DIR, name), apply))
    print(f"source-titles: {n} source notes updated")
    return 0


def _strip_bo(s):
    """Reduce Tibetan to comparable letters: drop punctuation, the tsheg, the
    ornamental yig-mgo, quote markers and whitespace."""
    return re.sub(r"[༄༅།༈་\s༎༏༐༑༔>|]+", "", s or "")


def _retitle_body(body, title, bo_heading):
    """Give the file a target-language H1 while keeping the Tibetan title, and
    stop the machine's own rendering of the title from standing as a line of
    translation.

    Two edits, both idempotent:

    1. ``# <Tibetan> ^0`` becomes ``# <researched title> ^0`` with the Tibetan
       kept as a quoted line beneath it — the same source-above-translation
       shape every other block in the file uses. The quoted line is followed by
       a blank line, or it would be absorbed into the next block's source quote.
    2. Where a *block* is itself the title (the text opens by naming itself, or
       carries the ``རྒྱ་གར་སྐད་དུ།`` Sanskrit-title formula), the machine
       translation of that block is replaced by the researched title. The block
       and its id are kept: every source block must still appear exactly once.
    """
    n = 0
    m = re.search(r"^# (.*?)(\s*\^0)\s*$", body, re.M)
    if m:
        current = m.group(1).strip()
        anchor = m.group(2)
        if current != title:
            tail = body[m.end():]
            # If a previous run already parked the Tibetan heading below the H1,
            # take it back off rather than stacking a second copy.
            existing = re.match(r"\n+> ([^\n]*)\n", tail)
            if existing and _strip_bo(existing.group(1)) == _strip_bo(current):
                tail = tail[existing.end():].lstrip("\n")
                tail = "\n" + tail
            body = body[:m.start()] + f"# {title}{anchor}\n\n> {current}\n" + tail
            n += 1
            bo_heading = bo_heading or current

    body, n2 = _retitle_title_block(body, title, bo_heading)
    return body, n + n2


def _retitle_title_block(body, title, bo_heading):
    """Replace the translation of a block that is itself the work's title."""
    target = _strip_bo(bo_heading)
    if not target:
        return body, 0

    # A rendered block is: quoted source lines, blank line, translation lines,
    # the last of which carries the ` ^id` anchor.
    pattern = re.compile(
        r"(?m)^((?:> [^\n]*\n)+)\n((?:[^\n>#][^\n]*\n?)+?)[ \t]*\^(\S+)[ \t]*$")

    out, pos, n = [], 0, 0
    for mb in pattern.finditer(body):
        quote, translation, bid = mb.group(1), mb.group(2), mb.group(3)
        raw = " ".join(l[2:] for l in quote.strip().split("\n"))
        src = _strip_bo(raw)
        # A block counts as the title ONLY if it *is* the title, or if it is the
        # `རྒྱ་གར་སྐད་དུ། … བོད་སྐད་དུ། <title>` bilingual title formula.
        #
        # Mere containment is not enough and was actively wrong: a colophon
        # ("ཅེས་<title>་དཔལ་ཀརྨ་པས་བྲིས་པ…", "thus the <title>, written by…"), an
        # end-marker ("<title>་རྫོགས་སོ།།"), and an opening verse that names the
        # instruction it is about all contain the title while being ordinary
        # translatable content. Replacing those destroys real translation.
        is_title = src == target or ("བོད་སྐད་དུ" in raw and target in src)
        if not is_title:
            continue
        if translation.strip() == title:
            continue  # already done
        out.append(body[pos:mb.start()])
        out.append(f"{quote}\n{title} ^{bid}\n")
        pos = mb.end() + 1 if body[mb.end():mb.end() + 1] == "\n" else mb.end()
        n += 1
    if not n:
        return body, 0
    out.append(body[pos:])
    return "".join(out), n


# --------------------------------------------------------------------------


def pass_authors():
    """Stamp backend person ids onto each source note's `author:` field.

    build_payloads.py reads the ids back out of that one field, one contributor
    per `;`:

        author: གླང་རི་ཐང་པ། [person:u4IF…] [bdrc:P3445] [role:author]

    They live here rather than in a key of their own because the payload
    builder already parses bracket tags off `author`, and because a note
    rebuilt by `block_ids.py stamp 0-INBOX` keeps only what the inbox had —
    so, exactly like text_id/edition_id and the researched titles, the ids
    must be re-applied from a registry (1-SOURCES/liturgy-persons.json)
    rather than being hand-typed into the note and lost on the next rebuild.

    Idempotent: existing bracket tags are stripped and rewritten, so running
    twice changes nothing. Authors with no confident match are left bare.
    """
    if not os.path.exists(PERSONS):
        print(f"authors: {PERSONS} not found — build it first", file=sys.stderr)
        return 1
    with open(PERSONS) as fh:
        reg = {a["author"]: a for a in json.load(fh)["authors"]}

    n = unmatched = 0
    for name in sorted(os.listdir(SRC_DIR)):
        if not name.endswith(".md"):
            continue

        def apply(fm, body):
            line = find_key(fm, "author")
            if line is None:
                return False
            raw = fm[line].split(":", 1)[1].strip()
            bare = re.sub(r"\s*\[(?:person|bdrc|role):[^\]]*\]", "", raw).strip()
            if not bare:
                # `author:` present but empty — the text is anonymous. That is a
                # real state, not a gap, and must not read as "unresolved".
                return set_key(fm, "contributor_status", "none", after="author")
            rec = reg.get(bare)
            if not rec or not rec.get("person_id"):
                # No confident match: strip any stale tag and say so plainly, so
                # a reader can tell "nobody resolved this yet" apart from
                # "this text genuinely has no named author".
                a = set_key(fm, "author", bare)
                b = set_key(fm, "contributor_status", "unresolved", after="author")
                return a or b
            tags = f"[person:{rec['person_id']}]"
            if rec.get("person_bdrc"):
                tags += f" [bdrc:{rec['person_bdrc']}]"
            tags += f" [role:{rec.get('role') or 'author'}]"
            a = set_key(fm, "author", f"{bare} {tags}")
            b = set_key(fm, "contributor_status", "resolved", after="author")
            return a or b

        if edit(os.path.join(SRC_DIR, name), apply):
            n += 1

    for a, rec in reg.items():
        if not rec.get("person_id"):
            unmatched += 1
    print(f"authors: {n} source notes updated; {unmatched} author strings still unmatched")
    print("         contributor_status written on every note: "
          "resolved | unresolved | none")
    return 0


# --------------------------------------------------------------------------
# pass 6 — tags (live backend -> vault frontmatter)
# --------------------------------------------------------------------------

def _live_tag_map():
    """-> {text_id: [tag_id, ...]} for every text the library lists."""
    import urllib.parse
    import urllib.request
    app = os.environ.get("WEBUDDHIST_APP") or "webuddhist"
    out, offset = {}, 0
    while True:
        q = urllib.parse.urlencode({"limit": 100, "offset": offset})
        req = urllib.request.Request(f"{API_BASE}/v2/texts?{q}",
                                     headers={"X-Application": app})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.load(r)
        for t in page["items"]:
            out[t["id"]] = list(t.get("tag_ids") or [])
        if not page.get("has_more"):
            return out
        offset += 100


def pass_tags():
    with open(TAG_REGISTRY) as fh:
        reg = json.load(fh)["tags"]
    id_to_key = {spec["id"]: key for key, spec in reg.items() if spec.get("id")}
    live = _live_tag_map()
    unknown_ids, no_id, not_live = set(), [], []
    n_src = n_tr = 0

    def apply_for(text_id, fm, label):
        if not text_id:
            no_id.append(label)
            return False
        if text_id not in live:
            not_live.append(label)
            return False
        # sort by key so the rendered lists are stable across runs, whatever
        # order the backend happens to return the ids in
        pairs = []
        for i in live[text_id]:
            if i in id_to_key:
                pairs.append((id_to_key[i], i))
            else:
                unknown_ids.add(i)
                pairs.append((i, i))
        pairs.sort()
        keys = [k for k, _ in pairs]
        ids = [i for _, i in pairs]
        a = set_list(fm, "tags", keys, after=["edition_id", "text_id", "category_id"])
        b = set_list(fm, "tag_ids", ids, after="tags")
        return a or b

    for name in sorted(os.listdir(SRC_DIR)):
        if not name.endswith(".md"):
            continue

        def apply(fm, body, name=name):
            return apply_for(get_key(fm, "text_id"), fm, f"source {name[:-3]}")

        n_src += bool(edit(os.path.join(SRC_DIR, name), apply))

    for stem, path in track_files():
        def apply(fm, body, path=path):
            tid = get_key(fm, "text_id") or get_key(fm, "translation_of_text_id")
            return apply_for(tid, fm, f"track {os.path.relpath(path, VAULT)}")

        n_tr += bool(edit(path, apply))

    print(f"tags: {n_src} source notes, {n_tr} translation files updated "
          f"({len(live)} live texts, {len(id_to_key)} registry tags)")
    for label in no_id:
        print(f"  !! no text_id: {label}", file=sys.stderr)
    for label in not_live:
        print(f"  !! text_id not found on backend: {label}", file=sys.stderr)
    for i in sorted(unknown_ids):
        print(f"  !! tag id not in registry (kept as id): {i}", file=sys.stderr)
    return int(bool(no_id or not_live))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", action="store_true")
    ap.add_argument("--track-meta", action="store_true")
    ap.add_argument("--titles", action="store_true")
    ap.add_argument("--source-titles", action="store_true")
    ap.add_argument("--authors", action="store_true")
    ap.add_argument("--tags", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--track", default=None,
                    help="restrict the translation passes to one track folder — "
                         "use this when another track is being written by a live "
                         "translation run")
    args = ap.parse_args()
    if args.track:
        global ONLY_TRACK
        ONLY_TRACK = args.track
    if not any([args.ids, args.track_meta, args.titles, args.source_titles,
                args.authors, args.tags, args.all]):
        ap.error("choose at least one pass")

    rc = 0
    if args.ids or args.all:
        rc |= pass_ids()
    if args.track_meta or args.all:
        rc |= pass_track_meta()
    if args.titles or args.all:
        rc |= pass_titles()
    if args.source_titles or args.all:
        rc |= pass_source_titles()
    if args.authors or args.all:
        rc |= pass_authors()
    if args.all or args.tags:
        rc |= pass_tags()
    return rc


if __name__ == "__main__":
    sys.exit(main())
