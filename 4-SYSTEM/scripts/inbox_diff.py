#!/usr/bin/env python3
"""Compare the expert-edited notes in 0-INBOX against the stamped copies in
1-SOURCES/Text — the exact state that was uploaded to the WeBuddhist library —
and report, per text, what changed and what that change costs to push.

Block ids (` ^k-n` on the last line of a unit) are annotations, not text: the
source copy is run through ``block_ids.strip_text`` first, so the comparison
never sees them (an inbox note that carries ids is stripped too, and flagged).
Pipeline-owned frontmatter keys (backend ids, researched titles, `status`, the
`[person:…]` tags stamped onto `author`) are ignored the same way. Everything
else is compared at three depths:

    letters      Tibetan letters/vowels only — a difference here is a real
                 text change: a syllable added, dropped or flipped.
    punctuation  letters plus shad/tsheg/ornaments, whitespace removed — a
                 difference here (with letters equal) is a punctuation edit.
    raw          the bytes — a difference here alone is whitespace.

Units (headings and blank-line-separated segments) are aligned old↔new on
their letters-only key, so a segment that was split, merged, inserted or
removed is reported as exactly that, and a re-segmentation of unchanged text
is never mistaken for a rewrite. Block ids are then re-derived for the inbox
body and compared with the source's, because a renumbered id is what breaks
the six translation alignments on the backend.

Every text is judged twice: as the inbox stands, and again with the
systematic "title repeated as the first body line" edit set aside, so that
one corpus-wide change does not hide what else moved.

Usage (from the vault root):

    inbox_diff.py                       summary to stdout
    inbox_diff.py --report 4-SYSTEM/inbox-diff-report.md --json 4-SYSTEM/inbox-diff-report.json
    inbox_diff.py --only <source stem>  one text, full detail to stdout
    inbox_diff.py --live                also GET each live edition's content and
                                        confirm the source copy still matches it
                                        (read-only; needs WEBUDDHIST_API_KEY)

Nothing here writes to 0-INBOX, 1-SOURCES or the backend.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.dirname(os.path.dirname(HERE))
INBOX = os.path.join(VAULT, "0-INBOX")
SRC_DIR = os.path.join(VAULT, "1-SOURCES", "Text")
LEDGER = os.path.join(HERE, "upload_ledger.json")
RETIRED = os.path.join(VAULT, "4-SYSTEM", "retired-texts.txt")
BASE = "https://library.webuddhist.com"
LANGS = ("en", "zh", "hi", "ne", "mn", "vi")

sys.path.insert(0, HERE)
import block_ids as B  # noqa: E402  (the stamping script; strip/parse/assign are reused)

# Frontmatter keys the pipeline writes; never an expert edit.
PIPELINE_KEYS = {
    # the translated titles, written by `stamp_metadata.py --source-titles` as
    # `alt_titles` entries (formerly title_en … title_vi); the inbox notes carry none
    "alt_titles",
    "contributor_status", "text_id", "edition_id", "status",
}
# Where an edited key would have to go on the backend.
TEXT_KEYS = {"title", "author", "license", "bdrc_work_id", "date", "category_id",
             "language", "lang_tag"}          # PATCH /v2/texts/{id}
EDITION_KEYS = {"source", "edition_type"}     # no update endpoint exists
TAG_RE = re.compile(r"\s*\[(?:person|bdrc|role):[^\]]*\]")

# Everything that is not a letter: whitespace, the Tibetan punctuation and
# symbol ranges (digits U+0F20–0F33 are kept — a digit is a character), and
# ASCII punctuation. ༀ (U+0F00) is a syllable and is kept.
NONLETTER_RE = re.compile(
    r"[\s༁-༟༴-༿྅྾-࿚"
    r"()\[\]{}<>.,;:!?'\"\-_#*^~`|/\\]+"
)
WS_RE = re.compile(r"\s+")
BAD_HEADING_RE = re.compile(r"^#{1,6}[^#\s]")
CTX = 12          # chars of context around a hunk
MAX_HUNKS = 60    # per text, in the report

# Events that move a block boundary or an id: the segmentation must be re-posted.
BOUNDARY_EVENTS = {"resegmented", "text+resegmented", "segment-added", "segment-removed",
                   "title-line-added", "linebreaks", "heading-added", "heading-removed"}
# Events the backend can take as in-place content operations.
INPLACE_EVENTS = {"text", "punctuation", "whitespace",
                  "heading-changed", "heading-punctuation", "heading-whitespace"}


def letters(s):
    return NONLETTER_RE.sub("", s)


def nows(s):
    return WS_RE.sub("", s)


def norm(stem):
    """Inbox names carry a decorative trailing shad/space; source names do not.
    Same rule as build_review_bundle.py."""
    return re.sub(r"[\s།]+$", "", stem)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_fm(fm_text):
    out = {}
    for line in fm_text.split("\n"):
        m = re.match(r"^([A-Za-z_][\w]*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def load_retired():
    stems = set()
    if os.path.exists(RETIRED):
        for line in read(RETIRED).split("\n"):
            line = line.split("#", 1)[0].strip()
            if line:
                stems.add(line[:-3] if line.endswith(".md") else line)
    return stems


def git_head():
    """Short hash of HEAD. `git` itself may be unusable (Apple's /usr/bin/git
    stub refuses to run until the Xcode licence is accepted), so fall back to
    reading .git directly."""
    for git in ("git", "/opt/homebrew/bin/git", "/usr/local/bin/git"):
        try:
            p = subprocess.run([git, "log", "-1", "--format=%h %ci"], cwd=VAULT,
                               capture_output=True, text=True)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except OSError:
            continue
    try:
        head = read(os.path.join(VAULT, ".git", "HEAD")).strip()
        if head.startswith("ref: "):
            ref = head[5:]
            ref_path = os.path.join(VAULT, ".git", ref)
            if os.path.exists(ref_path):
                return read(ref_path).strip()[:7]
            for line in read(os.path.join(VAULT, ".git", "packed-refs")).split("\n"):
                if line.endswith(" " + ref):
                    return line.split()[0][:7]
        return head[:7]
    except OSError:
        return "?"


# --------------------------------------------------------------------------
# units
# --------------------------------------------------------------------------

class U:
    """A parsed unit with the keys the comparison needs."""
    __slots__ = ("kind", "level", "text", "lines", "idx", "bid", "key")

    def __init__(self, unit):
        self.kind = unit.kind
        self.level = unit.level
        self.lines = list(unit.lines)
        self.idx = unit.idx
        self.bid = unit.bid
        if unit.kind == "heading":
            m = B.HEADING_RE.match(unit.lines[0])
            self.text = m.group(2)
            self.key = ("H", unit.level, letters(self.text))
        else:
            self.text = "\n".join(unit.lines)
            self.key = ("S", letters(self.text))

    def label(self):
        t = self.text.strip().replace("\n", " ⏎ ")
        return (t[:48] + "…") if len(t) > 48 else t

    def ref(self):
        return f"^{self.bid}" if self.bid is not None else "—"


def parse(body):
    """Units of a body plus the ids block_ids would assign. ``abort`` carries
    the reason if the stamper would refuse the file."""
    _, units = B.parse_body(body)
    abort = None
    try:
        B.assign_ids(units)
    except B.Abort as exc:
        abort = str(exc)
    return [U(u) for u in units], abort


def title_line_span(body):
    """(start, end) line indexes of a first segment that merely repeats the H1
    (plus the blank run after it), or None. This is the corpus-wide edit that
    the second verdict sets aside."""
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None
    m = B.HEADING_RE.match(lines[i])
    if not m or len(m.group(1)) != 1:
        return None
    h1 = letters(m.group(2))
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines) or B.HEADING_RE.match(lines[j]) or letters(lines[j]) != h1:
        return None
    k = j + 1
    if k < len(lines) and lines[k].strip():
        return None                       # a multi-line first segment is real text
    while k < len(lines) and not lines[k].strip():
        k += 1
    return j, k


def drop_title_line(body):
    span = title_line_span(body)
    if not span:
        return body
    lines = body.split("\n")
    return "\n".join(lines[:span[0]] + lines[span[1]:])


# --------------------------------------------------------------------------
# character-level hunks
# --------------------------------------------------------------------------

def show(s):
    return s.replace("\n", "⏎")


def char_hunks(old, new, ctx=CTX, gap=3):
    """Compact `…ctx[-old-]{+new+}ctx…` hunks between two strings. Adjacent
    edits closer than `gap` chars are merged into one hunk."""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if not ops:
        return []
    groups, cur = [], [ops[0]]
    for op in ops[1:]:
        if op[1] - cur[-1][2] <= gap:
            cur.append(op)
        else:
            groups.append(cur)
            cur = [op]
    groups.append(cur)

    hunks = []
    for g in groups:
        i1, j1 = g[0][1], g[0][3]
        i2, j2 = g[-1][2], g[-1][4]
        o, n = old[i1:i2], new[j1:j2]
        pre = old[max(0, i1 - ctx):i1]
        post = old[i2:i2 + ctx]
        body = f"[-{show(o)}-]" if o else ""
        body += f"{{+{show(n)}+}}" if n else ""
        hunks.append({
            "at": i1,
            "old": o,
            "new": n,
            "kind": ("whitespace" if nows(o) == nows(n)
                     else "punctuation" if letters(o) == letters(n)
                     else "text"),
            "render": f"…{show(pre)}{body}{show(post)}…",
        })
    return hunks


def classify_pair(old_text, new_text):
    """How two texts with the same letters differ."""
    if old_text == new_text:
        return None
    if nows(old_text) == nows(new_text):
        return "whitespace"
    return "punctuation"


# --------------------------------------------------------------------------
# one text
# --------------------------------------------------------------------------

def compare_metadata(fm_src, fm_in):
    changes = []
    for k in fm_in:
        if k in PIPELINE_KEYS:
            continue
        new = fm_in[k]
        if k not in fm_src:
            changes.append({"key": k, "change": "added", "old": None, "new": new})
            continue
        old = fm_src[k]
        if k == "author":
            old = TAG_RE.sub("", old).strip()
        if old != new:
            changes.append({"key": k, "change": "changed", "old": old, "new": new})
    for k in fm_src:
        if k not in fm_in and k not in PIPELINE_KEYS:
            changes.append({"key": k, "change": "removed", "old": fm_src[k], "new": None})
    for c in changes:
        c["target"] = ("text" if c["key"] in TEXT_KEYS
                       else "edition" if c["key"] in EDITION_KEYS else "vault-only")
    return changes


def diff_bodies(body_s, body_i, path_i):
    """Unit-level events, character hunks and id impact between two bodies."""
    r = {"changes": [], "hunks": [], "defects": []}
    old, abort_s = parse(body_s)
    new, abort_i = parse(body_i)
    if abort_i:
        r["defects"].append(f"block_ids would refuse to stamp this file: {abort_i}")
    r["stamp_abort"] = abort_i

    h1_letters = next((u.key[2] for u in new if u.kind == "heading" and u.level == 1), None)
    sm = difflib.SequenceMatcher(None, [u.key for u in old], [u.key for u in new], autojunk=False)
    pairs = []            # (old_unit, new_unit) aligned by letters
    ev = r["changes"]
    hunks = r["hunks"]

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for a, b in zip(old[i1:i2], new[j1:j2]):
                pairs.append((a, b))
                if a.text == b.text:
                    continue
                if a.kind == "heading":
                    ev.append({"type": "heading-" + classify_pair(a.text, b.text),
                               "old": a.text, "new": b.text, "old_id": a.ref(), "new_id": b.ref()})
                    hunks += [dict(h, unit=f"heading {a.ref()}") for h in char_hunks(a.text, b.text)]
                    continue
                kind = classify_pair(a.text, b.text)
                la, lb = [letters(x) for x in a.lines], [letters(x) for x in b.lines]
                if len(a.lines) != len(b.lines) or la != lb:
                    ev.append({"type": "linebreaks", "old_id": a.ref(), "new_id": b.ref(),
                               "old_lines": len(a.lines), "new_lines": len(b.lines), "label": a.label()})
                    if kind == "whitespace":
                        continue
                ev.append({"type": kind, "old_id": a.ref(), "new_id": b.ref(), "label": a.label()})
                hunks += [dict(h, unit=a.ref()) for h in char_hunks(a.text, b.text)]
            continue

        if tag == "insert":
            for b in new[j1:j2]:
                if b.kind == "heading":
                    ev.append({"type": "heading-added", "level": b.level, "new": b.text, "new_id": b.ref()})
                elif (j1 == 1 and len(b.lines) == 1 and h1_letters is not None
                      and b.key[1] == h1_letters):
                    ev.append({"type": "title-line-added", "new_id": b.ref(), "new": b.text})
                else:
                    ev.append({"type": "segment-added", "new_id": b.ref(), "label": b.label(),
                               "lines": len(b.lines)})
            continue

        if tag == "delete":
            for a in old[i1:i2]:
                if a.kind == "heading":
                    ev.append({"type": "heading-removed", "level": a.level, "old": a.text, "old_id": a.ref()})
                else:
                    ev.append({"type": "segment-removed", "old_id": a.ref(), "label": a.label(),
                               "lines": len(a.lines)})
            continue

        # replace: a run of old units vs a run of new units with different letters
        oh = [u for u in old[i1:i2] if u.kind == "heading"]
        nh = [u for u in new[j1:j2] if u.kind == "heading"]
        os_ = [u for u in old[i1:i2] if u.kind == "segment"]
        ns = [u for u in new[j1:j2] if u.kind == "segment"]
        for a, b in zip(oh, nh):
            pairs.append((a, b))
            ev.append({"type": "heading-changed", "old": a.text, "new": b.text,
                       "old_id": a.ref(), "new_id": b.ref()})
            hunks += [dict(h, unit=f"heading {a.ref()}") for h in char_hunks(a.text, b.text)]
        for a in oh[len(nh):]:
            ev.append({"type": "heading-removed", "level": a.level, "old": a.text, "old_id": a.ref()})
        for b in nh[len(oh):]:
            ev.append({"type": "heading-added", "level": b.level, "new": b.text, "new_id": b.ref()})

        if not os_ and not ns:
            continue
        old_join = "\n".join(u.text for u in os_)
        new_join = "\n".join(u.text for u in ns)
        unit_lbl = "+".join(u.ref() for u in os_) or "—"
        if letters(old_join) == letters(new_join):
            # the words are the same; only the block boundaries moved
            ev.append({"type": "resegmented", "old_ids": [u.ref() for u in os_],
                       "new_ids": [u.ref() for u in ns], "old_n": len(os_), "new_n": len(ns),
                       "label": (os_ or ns)[0].label()})
            kind = classify_pair(nows(old_join), nows(new_join))
            if kind:
                ev.append({"type": kind, "old_id": os_[0].ref() if os_ else "—",
                           "new_id": ns[0].ref() if ns else "—", "label": (os_ or ns)[0].label()})
                hunks += [dict(h, unit=unit_lbl) for h in char_hunks(old_join, new_join)]
            continue
        if len(os_) == len(ns):
            for a, b in zip(os_, ns):
                pairs.append((a, b))
                ev.append({"type": "text", "old_id": a.ref(), "new_id": b.ref(), "label": a.label()})
                if len(a.lines) != len(b.lines):
                    ev.append({"type": "linebreaks", "old_id": a.ref(), "new_id": b.ref(),
                               "old_lines": len(a.lines), "new_lines": len(b.lines), "label": a.label()})
                hunks += [dict(h, unit=a.ref()) for h in char_hunks(a.text, b.text)]
        else:
            ev.append({"type": "text+resegmented", "old_ids": [u.ref() for u in os_],
                       "new_ids": [u.ref() for u in ns], "old_n": len(os_), "new_n": len(ns),
                       "label": (os_ or ns)[0].label()})
            hunks += [dict(h, unit=unit_lbl) for h in char_hunks(old_join, new_join)]

    # --- block-id impact -------------------------------------------------
    new_ids = {u.bid for u in new}
    r["ids"] = {
        "old_total": len(old),
        "new_total": len(new),
        "kept": sum(1 for a, b in pairs if a.bid == b.bid),
        "renumbered": sum(1 for a, b in pairs if a.bid != b.bid),
        "dropped": [u.bid for u in old if u.bid not in new_ids],
        "old_scheme": "sectioned" if any(u.kind == "heading" and u.level == 2 for u in old) else "flat",
        "new_scheme": "sectioned" if any(u.kind == "heading" and u.level == 2 for u in new) else "flat",
    }

    # --- counts & verdict ------------------------------------------------
    c = {}
    for e in ev:
        c[e["type"]] = c.get(e["type"], 0) + 1
    r["counts"] = c
    hk = {}
    for h in hunks:
        hk[h["kind"]] = hk.get(h["kind"], 0) + 1
    r["hunk_counts"] = hk
    r["hunks"] = hunks[:MAX_HUNKS]

    if body_s == body_i:
        r["body_verdict"] = "unchanged"
    elif abort_i:
        r["body_verdict"] = "blocked"          # cannot even be stamped yet
    elif (c.keys() & BOUNDARY_EVENTS) or r["ids"]["renumbered"] or r["ids"]["dropped"]:
        r["body_verdict"] = "rebuild"          # segmentation must be re-posted
    elif c.keys() & INPLACE_EVENTS:
        r["body_verdict"] = "patch"            # PATCH content ops keep spans valid
    elif not ev and len(pairs) == len(old) == len(new):
        # Bytes differ but no unit does: only the contents of blank runs
        # (a lone-space "blank" line, a final newline). The parser never
        # sees those, so the backend copy is unaffected.
        ev.append({"type": "blank-lines"})
        c["blank-lines"] = 1
        r["body_verdict"] = "unchanged"
    else:
        r["body_verdict"] = "rebuild"
    return r


def compare_text(stem, src_path, in_path, ledger):
    src_raw = read(src_path)
    in_raw = read(in_path)
    src_stripped = B.strip_text(src_raw, src_path)     # ids removed, bytes otherwise intact
    defects = []
    if B.is_stamped(in_raw, in_path):
        defects.append("inbox note carries block ids of its own (stripped before comparing; "
                       "0-INBOX notes must not carry ids)")
        in_raw = B.strip_text(in_raw, in_path)
    fm_s, body_s = B.split_frontmatter(src_stripped, src_path)
    fm_i, body_i = B.split_frontmatter(in_raw, in_path)

    # Lines that look like headings but will not parse as one (`##Title`):
    # block_ids treats them as segment text and the `##` leaks into content.
    for n, line in enumerate(body_i.split("\n"), 1):
        if BAD_HEADING_RE.match(line):
            defects.append(f"line {n}: heading without a space after the hashes: {line.strip()[:40]!r}")

    r = {
        "stem": stem,
        "inbox_file": os.path.basename(in_path),
        "source_file": os.path.basename(src_path),
        "text_id": ledger.get(stem, {}).get("text_id"),
        "edition_id": ledger.get(stem, {}).get("edition_id"),
        "translations_live": [l for l in LANGS if ledger.get(f"{stem}-{l}", {}).get("aligned")],
        "metadata": compare_metadata(read_fm(fm_s), read_fm(fm_i)),
        "body_identical": body_s == body_i,
    }
    full = diff_bodies(body_s, body_i, in_path)
    r.update(full)
    r["defects"] = defects + full["defects"]

    # Second verdict: the same comparison with the repeated title line set aside.
    alt_body = drop_title_line(body_i)
    if alt_body != body_i:
        alt = diff_bodies(body_s, alt_body, in_path)
        r["without_title_line"] = {k: alt[k] for k in ("body_verdict", "counts", "ids", "hunk_counts")}
        r["without_title_line"]["body_identical"] = body_s == alt_body
    else:
        r["without_title_line"] = {k: r[k] for k in ("body_verdict", "counts", "ids", "hunk_counts")}
        r["without_title_line"]["body_identical"] = r["body_identical"]

    r["update"] = plan(r, r["body_verdict"], r["ids"], len(r["hunks"]))
    a = r["without_title_line"]
    r["without_title_line"]["update"] = plan(r, a["body_verdict"], a["ids"],
                                             sum(a["hunk_counts"].values()))
    return r


def plan(r, verdict, ids, n_hunks):
    """Backend calls this text needs, given the diff. Describes, never sends.
    Returns (code, steps): a short code for tables and the call list."""
    steps = []
    codes = []
    n_tr = len(r["translations_live"])
    tk = [m["key"] for m in r["metadata"] if m["target"] == "text"]
    ek = [m["key"] for m in r["metadata"] if m["target"] == "edition"]
    if tk:
        steps.append(f"PATCH /v2/texts/{{text_id}} ({', '.join(tk)})")
        codes.append("M")
    if ek:
        steps.append(f"edition metadata ({', '.join(ek)}) — NO ENDPOINT")
        codes.append("M!")
    if verdict == "blocked":
        steps.append("fix the defect in 0-INBOX first — the note cannot be stamped")
        codes.append("X")
    elif verdict == "patch":
        steps.append(f"{n_hunks} × PATCH /v2/editions/{{edition_id}}/content "
                     f"(spans auto-adjust; alignments survive)")
        codes.append(f"P{n_hunks}")
    elif verdict == "rebuild":
        steps.append("DELETE /v2/editions/{edition_id}/segmentation (drops every alignment)")
        steps.append("PATCH /v2/editions/{edition_id}/content ×1 (replace 0..len with the new content)")
        steps.append("POST /v2/editions/{edition_id}/segmentation")
        if n_tr:
            steps.append(f"PUT /v2/editions/{{edition_id}}/alignments/{{translation}} × {n_tr} "
                         f"({', '.join(r['translations_live'])})")
        codes.append(f"R+A{n_tr}" if n_tr else "R")
        if ids.get("new_scheme") == "sectioned":
            steps.append("POST /v2/editions/{edition_id}/table-of-contents (headings now exist)")
            codes.append("T")
    return {"code": " ".join(codes) or "—", "steps": steps}


# --------------------------------------------------------------------------
# live check (read-only)
# --------------------------------------------------------------------------

_PARSER = None


def expected_content(src_path):
    """The content string the vendored upstream parser derives from a stamped
    source note — the same code path `liturgy_payloads.py` uploaded through,
    so the live edition should equal it byte for byte."""
    global _PARSER
    if _PARSER is None:
        import importlib.util
        import pathlib
        p = os.path.join(HERE, "parser-root-text", "parser.py")
        spec = importlib.util.spec_from_file_location("wb_parser", p)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(p))
        spec.loader.exec_module(mod)
        _PARSER = (mod, pathlib.Path)
    P, Path = _PARSER
    fm, body = P._read_source(Path(src_path))
    blocks = P._extract_blocks(body)
    doc_default = "paragraph" if fm.get("commentary_of") else "verse"
    content, _ = P._build_content_and_segmentation(blocks, doc_default)
    return content


def live_check(results):
    import urllib.request
    key = os.environ.get("WEBUDDHIST_API_KEY", "")
    app = os.environ.get("WEBUDDHIST_APP", "")
    if not key:
        print("--live needs WEBUDDHIST_API_KEY in the environment", file=sys.stderr)
        return
    for r in results:
        eid = r.get("edition_id")
        if not eid:
            r["live"] = "no edition id in ledger"
            continue
        req = urllib.request.Request(f"{BASE}/v2/editions/{eid}/content")
        req.add_header("X-API-Key", key)
        if app:
            req.add_header("X-Application", app)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                live = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            r["live"] = f"GET failed: {exc}"
            continue
        exp = expected_content(os.path.join(SRC_DIR, r["source_file"]))
        if live == exp:
            r["live"] = "matches source"
        else:
            hs = char_hunks(live, exp)
            r["live"] = (f"DIFFERS from source ({len(live)} vs {len(exp)} chars): "
                         + " ".join(h["render"] for h in hs[:3]))


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

EVENT_LABELS = {
    "title-line-added": "title repeated as first body line",
    "heading-added": "heading added",
    "heading-removed": "heading removed",
    "heading-changed": "heading text changed",
    "heading-punctuation": "H1 punctuation",
    "heading-whitespace": "H1 whitespace",
    "text": "text changed (letters)",
    "punctuation": "punctuation only",
    "whitespace": "whitespace only",
    "linebreaks": "line breaks moved",
    "resegmented": "re-segmented (same words)",
    "text+resegmented": "text changed and re-segmented",
    "segment-added": "segment added",
    "segment-removed": "segment removed",
    "blank-lines": "blank-line bytes only (no effect on the backend)",
}
SYSTEMATIC = {"title-line-added", "heading-added", "blank-lines"}


def verdict_word(r, alt=False):
    src = r["without_title_line"] if alt else r
    if src["body_verdict"] == "unchanged" and not r["metadata"]:
        return "unchanged"
    if src["body_verdict"] == "unchanged":
        return "metadata only"
    return src["body_verdict"]


def short_changes(counts, metadata):
    bits = []
    for t, n in sorted(counts.items(), key=lambda x: -x[1]):
        bits.append(f"{EVENT_LABELS.get(t, t)} ×{n}" if n > 1 else EVENT_LABELS.get(t, t))
    if metadata:
        bits.append("metadata: " + ", ".join(m["key"] for m in metadata))
    return "; ".join(bits) or "—"


def md_report(results, unmatched_inbox, retired, head, live):
    out = []
    w = out.append
    n = len(results)
    w("# 0-INBOX vs 1-SOURCES/Text — what the experts changed\n")
    w(f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M} by `4-SYSTEM/scripts/inbox_diff.py` "
      f"at vault commit `{head}`. Block ids are stripped before comparing; pipeline-owned frontmatter "
      f"keys are ignored. Generated file — re-run the script rather than editing it.\n")

    def tally(alt):
        v = {}
        for r in results:
            k = verdict_word(r, alt)
            v[k] = v.get(k, 0) + 1
        return v
    v_full, v_alt = tally(False), tally(True)

    w("## Summary\n")
    w("| verdict | as the inbox stands | with the repeated title line set aside |")
    w("|---|---|---|")
    for k in ("unchanged", "metadata only", "patch", "rebuild", "blocked"):
        if k in v_full or k in v_alt:
            w(f"| {k} | {v_full.get(k, 0)} | {v_alt.get(k, 0)} |")
    w(f"| **compared** | {n} | {n} |")
    w("")
    w(f"Not compared: {len(unmatched_inbox)} inbox notes with no source note (the backlog) "
      f"and {len(retired)} retired note.\n")
    w("- **unchanged** — no heading, block, line or character differs once ids are stripped (at most the bytes "
      "of a blank line), and no expert-owned frontmatter key differs.")
    w("- **patch** — only in-place character edits. The backend takes each as one content operation and "
      "shifts every span itself; the six translation alignments survive.")
    w("- **rebuild** — a block boundary, heading or block id moved. The segmentation has to be deleted "
      "and re-posted, which drops every alignment on the edition until each is PUT again.")
    w("- **blocked** — the inbox note cannot be stamped as it stands; see *Defects*.")
    w("")

    # change types
    ev = {}
    for r in results:
        for t in r["counts"]:
            ev[t] = ev.get(t, 0) + 1
    w("### Change types, by number of texts affected\n")
    w("| change | texts |\n|---|---|")
    for t, c in sorted(ev.items(), key=lambda x: -x[1]):
        w(f"| {EVENT_LABELS.get(t, t)} | {c} |")
    w(f"| frontmatter (expert-owned keys) | {sum(1 for r in results if r['metadata'])} |")
    w("")

    # systematic patterns
    w("### The two corpus-wide edits\n")
    tl = [r for r in results if "title-line-added" in r["counts"]]
    w(f"**Title repeated as the first body line — {len(tl)} of {n} texts.** Under the `# ༄༅། །Title` "
      f"heading the inbox now carries the same title again as a plain one-line segment. If stamped as-is "
      f"it becomes a new block (`^1` in flat texts, `^0-1` under a heading), every later id shifts by one, "
      f"and the uploaded content would carry the title twice (the H1 already becomes the `title` segment). "
      f"The second column of every table below shows what remains once this edit is set aside.\n")
    variants = {}
    for r in results:
        hs = tuple(f"{'#' * e['level']} {e['new']}" for e in r["changes"] if e["type"] == "heading-added")
        if hs:
            variants.setdefault(hs, []).append(r["stem"])
    w(f"**Colophon headings added — {sum(len(v) for v in variants.values())} texts.** "
      f"A flat text that gains a `##` heading switches id scheme (`^n` → `^0-n`, `^1-0`, `^1-1-1`…), "
      f"so every id in it is renumbered even though not one word changed. Variants seen:\n")
    w("| headings added | texts |\n|---|---|")
    for hs, stems in sorted(variants.items(), key=lambda x: -len(x[1])):
        w(f"| {' + '.join(f'`{h}`' for h in hs)} | {len(stems)}" + (f" — {', '.join(stems)}" if len(stems) <= 3 else "") + " |")
    w("")

    # block-id impact
    for alt, title in ((False, "as the inbox stands"), (True, "with the title line set aside")):
        src = lambda r: (r["without_title_line"] if alt else r)  # noqa: E731
        ren = [r for r in results if src(r)["ids"].get("renumbered") or src(r)["ids"].get("dropped")]
        scheme = [r for r in results if src(r)["ids"]["old_scheme"] != src(r)["ids"]["new_scheme"]]
        tot_align = sum(len(r["translations_live"]) for r in ren)
        w(f"### Block-id impact — {title}\n")
        w(f"- {len(ren)} of {n} texts get different ids if 0-INBOX is re-stamped "
          f"(ids are the segment references the translation alignments point at).")
        w(f"- {len(scheme)} texts change id scheme (flat `^n` → sectioned `^k-n`).")
        w(f"- Alignments to re-PUT: {tot_align}.")
        w("")
    aborts = [r for r in results if r.get("stamp_abort")]
    if aborts:
        w(f"{len(aborts)} inbox note(s) would be refused by `block_ids.py stamp` as they stand — see *Defects*.\n")

    if live:
        lv = {}
        for r in results:
            lv[r.get("live", "?")] = lv.get(r.get("live", "?"), 0) + 1
        w("### Live check — `GET /v2/editions/{id}/content` against the source note\n")
        for k, c in lv.items():
            w(f"- {k}: {c}")
        w("")

    # per-text table
    w("## Every text\n")
    w("Backend-work codes: **M** `PATCH /v2/texts` for metadata · **M!** edition metadata, no endpoint · "
      "**P*n*** *n* content patches, alignments kept · **R** delete + re-post segmentation · "
      "**A*n*** re-PUT *n* translation alignments · **T** post a table of contents · **X** blocked by a defect.\n")
    w("| # | text (source stem) | verdict | what changed | ids kept | work | verdict w/o title line | ids kept | work |")
    w("|---|---|---|---|---|---|---|---|---|")
    order = {"blocked": 0, "rebuild": 1, "patch": 2, "metadata only": 3, "unchanged": 4}
    for i, r in enumerate(sorted(results, key=lambda x: (order[verdict_word(x, True)], x["stem"])), 1):
        ids, a = r["ids"], r["without_title_line"]
        w(f"| {i} | {r['stem']} | {verdict_word(r)} | {short_changes(r['counts'], r['metadata'])} | "
          f"{ids['kept']}/{ids['old_total']} | {r['update']['code']} | "
          f"{verdict_word(r, True)} | {a['ids']['kept']}/{a['ids']['old_total']} | {a['update']['code']} |")
    w("")

    # defects
    defects = [(r["stem"], d) for r in results for d in r["defects"]]
    w("## Defects to hand back to the expert\n")
    if not defects:
        w("None.\n")
    for stem, d in defects:
        w(f"- **{stem}** — {d}")
    w("")

    # metadata
    w("## Frontmatter changes\n")
    rows = [(r["stem"], m) for r in results for m in r["metadata"]]
    if not rows:
        w("No expert-owned key differs between any inbox note and its source.\n")
    else:
        w("| text | key | change | old | new | goes to |\n|---|---|---|---|---|---|")
        for stem, m in rows:
            w(f"| {stem} | `{m['key']}` | {m['change']} | {m['old'] or ''} | {m['new'] or ''} | {m['target']} |")
        w("")

    # detail
    w("## Detail — everything beyond the two corpus-wide edits\n")
    w("`[-old-]{+new+}` inside a dozen characters of context; `⏎` is a line break. "
      "The repeated title line and the added colophon headings are not repeated here.\n")
    any_detail = False
    for r in sorted(results, key=lambda x: x["stem"]):
        events = [e for e in r["changes"] if e["type"] not in SYSTEMATIC]
        if not events and not r["hunks"]:
            continue
        any_detail = True
        ids = r["ids"]
        w(f"### {r['stem']}\n")
        w(f"inbox `{r['inbox_file']}` · verdict **{verdict_word(r)}** (w/o title line: **{verdict_word(r, True)}**) "
          f"· ids kept {ids['kept']}/{ids['old_total']}"
          + (f" · scheme {ids['old_scheme']} → {ids['new_scheme']}" if ids['old_scheme'] != ids['new_scheme'] else "")
          + "\n")
        for e in events:
            t = e["type"]
            if t == "heading-removed":
                w(f"- heading removed: `{'#' * e['level']} {e['old']}` (was `{e['old_id']}`)")
            elif t in ("heading-changed", "heading-punctuation", "heading-whitespace"):
                w(f"- heading `{e['old_id']}` {EVENT_LABELS[t]}: `{e['old']}` → `{e['new']}`")
            elif t in ("resegmented", "text+resegmented"):
                w(f"- {EVENT_LABELS[t]}: {e['old_n']} block(s) `{'+'.join(e['old_ids'])}` "
                  f"→ {e['new_n']} block(s) `{'+'.join(e['new_ids'])}` — “{e['label']}”")
            elif t == "linebreaks":
                w(f"- `{e['old_id']}` line breaks moved: {e['old_lines']} → {e['new_lines']} lines — “{e['label']}”")
            elif t in ("segment-added", "segment-removed"):
                w(f"- {EVENT_LABELS[t]} (`{e.get('new_id') or e.get('old_id')}`, {e['lines']} line(s)): “{e['label']}”")
            elif t in ("text", "punctuation", "whitespace"):
                w(f"- `{e['old_id']}` {EVENT_LABELS[t]}"
                  + (f" (now `{e['new_id']}`)" if e['new_id'] != e['old_id'] else "") + f" — “{e['label']}”")
        if r["hunks"]:
            w("")
            w("| unit | kind | change |\n|---|---|---|")
            for h in r["hunks"]:
                w(f"| {h['unit']} | {h['kind']} | `{h['render']}` |")
        w("")
    if not any_detail:
        w("Nothing beyond the two corpus-wide edits.\n")

    # unmatched
    w("## Inbox notes without a source (not compared)\n")
    w("The backlog (`status: segmented`) and the retired note; none has been stamped or uploaded.\n")
    for f in sorted(unmatched_inbox):
        w(f"- {f}")
    for f in sorted(retired):
        w(f"- {f} (retired)")
    w("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inbox", default=INBOX)
    ap.add_argument("--sources", default=SRC_DIR)
    ap.add_argument("--only", help="compare just this source stem and print its detail")
    ap.add_argument("--report", help="write the markdown report here")
    ap.add_argument("--json", help="write the full machine-readable result here")
    ap.add_argument("--live", action="store_true",
                    help="also verify each source note against the live backend content (read-only)")
    args = ap.parse_args(argv)

    ledger = json.load(open(LEDGER, encoding="utf-8")) if os.path.exists(LEDGER) else {}
    retired = load_retired()

    sources = {f[:-3]: f for f in os.listdir(args.sources) if f.endswith(".md")}
    inbox = {}
    for f in sorted(os.listdir(args.inbox)):
        if f.endswith(".md"):
            inbox.setdefault(norm(f[:-3]), []).append(f)

    results, unmatched_src = [], []
    for stem in sorted(sources):
        if args.only and stem != args.only:
            continue
        cands = inbox.get(norm(stem), [])
        if not cands:
            unmatched_src.append(stem)
            continue
        if len(cands) > 1:
            print(f"note: {stem} matches {len(cands)} inbox files, using {cands[0]}", file=sys.stderr)
        results.append(compare_text(stem, os.path.join(args.sources, sources[stem]),
                                    os.path.join(args.inbox, cands[0]), ledger))

    matched_norms = {norm(s) for s in sources}
    retired_files, unmatched_inbox = [], []
    for k, files in inbox.items():
        if k in matched_norms:
            continue
        for f in files:
            (retired_files if f[:-3] in retired else unmatched_inbox).append(f)

    if args.live:
        live_check(results)

    head = git_head()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                       "head": head, "results": results,
                       "unmatched_inbox": unmatched_inbox, "retired": retired_files,
                       "sources_without_inbox": unmatched_src},
                      fh, ensure_ascii=False, indent=1)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(md_report(results, unmatched_inbox, retired_files, head, args.live))

    # stdout summary
    def tally(alt):
        v = {}
        for r in results:
            v[verdict_word(r, alt)] = v.get(verdict_word(r, alt), 0) + 1
        return ", ".join(f"{k} {n}" for k, n in sorted(v.items()))
    print(f"compared {len(results)} texts — as-is: {tally(False)} | without title line: {tally(True)}")
    if unmatched_src:
        print(f"sources with no inbox note: {len(unmatched_src)}")
    print(f"inbox notes with no source: {len(unmatched_inbox)} (+{len(retired_files)} retired)")
    if args.only:
        for r in results:
            print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        for r in results:
            if verdict_word(r, True) != "unchanged":
                print(f"  {verdict_word(r, True):13} {r['stem']}: "
                      f"{short_changes(r['without_title_line']['counts'], r['metadata'])}")
        if args.live:
            bad = [r for r in results if r.get("live") != "matches source"]
            print(f"live check: {len(results) - len(bad)} match the source note, {len(bad)} do not")
            for r in bad:
                print(f"  {r['stem']}: {r.get('live')}")
    if args.report:
        print(f"report: {args.report}")
    if args.json:
        print(f"json:   {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
