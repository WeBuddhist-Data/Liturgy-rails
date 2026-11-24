#!/usr/bin/env python3
"""Lint audio notes against the v2 audio API and build their upload payloads.

usage: lint_audio.py [NOTE_OR_FOLDER ...] [--live] [--host dev|testing|prod] [--hash]
                     [--audio-dir DIR] [--require-file] [--no-output] [-q]

With no path, lints every note under 1-SOURCES/Audio. Errors block the upload
(library.py refuses to write for a note with any error); warnings do not. Exit code is
1 when any note has an error.

  --live          also read the root text's edition from the API and check that
                  every timed Mapping row is a segment of it; this is what turns
                  the Mapping table into cues (spans). Without it cues are listed
                  by block id only.
  --hash          sha256 every audio file found and compare with the note (slow:
                  the WAVs are ~12 GB in all).
  --audio-dir     where the audio files are (default: $AUDIO_FILES_DIR, else
                  Audio/ at the vault root). The files are not in the repo.
  --require-file  a recording whose file is not found is an error (upload mode);
                  by default it is a warning, so notes lint without the files.

Writes output/<stem>.lint.json (no errors) or output/<stem>.lint.errors.json.
See README.md for every rule.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_note as an  # noqa: E402

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = an.VAULT / "0-INBOX" / "temp" / "linter-audio"     # generated output goes to the inbox
sys.path.insert(0, str(HERE.parent))
from webuddhist import envs as _envs  # noqa: E402  (environments: one list for every tool)

HOSTS = {name: e.url for name, e in _envs.ENVIRONMENTS.items()}

# v2 API enums (openapi: LicenseType, ContributorRole, AudioFormat)
LICENSES = {"cc0", "public", "cc-by", "cc-by-sa", "cc-by-nd", "cc-by-nc", "cc-by-nc-sa",
            "cc-by-nc-nd", "copyrighted", "unknown"}
AUDIO_ROLES = {"narrator", "singer", "musician"}
PERFORMANCE_ROLE = {"recitation": "narrator"}
DURATION_SLACK_MS = 500          # a cue may end this much after the recorded duration
SHORT_CUE_MS = 1000              # a cue shorter than this is suspicious

try:                              # the language list the root-text linter keeps fresh
    sys.path.insert(0, str(an.VAULT / "4-SYSTEM" / "scripts" / "linter-root-text"))   # the vault's shared linter
    from languages import LANGUAGE_VALUES  # type: ignore
except Exception:                 # noqa: BLE001
    LANGUAGE_VALUES = frozenset({"bo", "en", "zh", "sa", "pi", "lzh", "mn", "ja", "fr", "hi", "ne", "ru", "th", "vi"})

NORM = re.compile(r"[༄༅།༎་\s ​༑༔༏༐]+")


def norm(s):
    return NORM.sub("", s or "")


class Report:
    def __init__(self):
        self.errors, self.warnings, self.notes = [], [], []

    def error(self, code, msg):
        self.errors.append({"code": code, "message": msg})

    def warn(self, code, msg):
        self.warnings.append({"code": code, "message": msg})

    def note(self, code, msg):
        self.notes.append({"code": code, "message": msg})


# ------------------------------------------------------------------- API

def api_get(host, path):
    env = _envs.get(host)
    req = urllib.request.Request(env.url + path)
    for k, v in env.headers().items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


_EDITIONS = {}


def live_segments(host, edition_id):
    """-> (content, {reference: (start, end)}) for the edition, cached."""
    key = (host, edition_id)
    if key not in _EDITIONS:
        content = api_get(host, f"/v2/editions/{edition_id}/content")
        segs, off = {}, 0
        while True:
            page = api_get(host, f"/v2/editions/{edition_id}/segmentation/segments?limit=200&offset={off}")
            for s in page["items"]:
                if s.get("lines"):
                    segs[str(s.get("reference"))] = (s["lines"][0]["start"], s["lines"][-1]["end"])
            if not page.get("has_more") or not page["items"]:
                break
            off += len(page["items"])
        _EDITIONS[key] = (content, segs)
    return _EDITIONS[key]


# ----------------------------------------------------------------- rules

def check_metadata(n: an.AudioNote, r: Report):
    if str(n.fm.get("file_type", "")).strip() != "audio":
        r.error("file-type", f"file_type is {n.fm.get('file_type')!r}, expected 'audio'")

    # title: LocalizedString keyed by language code
    if not n.title:
        r.warn("title-missing", "no title: the audio is created without one")
    for k in n.title:
        if k == "?":
            r.error("title-lang", "title is set but lang_tag is not, so the title has no language key")
        elif k not in LANGUAGE_VALUES:
            r.error("title-lang", f"title key {k!r} is not a language code known to the API")
    if n.title and "en" not in n.title:
        r.warn("title-en", "no English title (title_en, or an `en:` entry in alt_titles)")

    if not n.language:
        r.warn("language", "no lang_tag: the API will use the recited text's language")
    elif n.language not in LANGUAGE_VALUES:
        r.error("language", f"lang_tag {n.language!r} is not a language code known to the API")

    # license: the API silently stores `public` when none is sent
    if n.license is None:
        r.error("license-missing", "no license: the API would store `public`, which nobody has "
                "stated. Set `license:` (use `unknown` if it is not known)")
    elif n.license not in LICENSES:
        r.error("license", f"license {n.license!r} is not one of {sorted(LICENSES)}")


def contributions(n: an.AudioNote, r: Report):
    """-> API contributions list. The API requires at least one (minItems 1)."""
    out = []
    if not n.contributors:
        r.error("contributor-missing", "no `artist`: the API requires at least one contributor")
        return out
    for c in n.contributors:
        role = c.role or PERFORMANCE_ROLE.get(n.performance_type or "")
        if not (c.op or c.bdrc):
            r.error("contributor-id", f"artist {c.name or c.raw!r} has no [op:ID] or [bdrc:ID]: it "
                    "cannot be sent, and the API requires at least one contributor. Create the "
                    "person (POST /v2/persons) and tag the artist line")
            continue
        if role is None:
            r.error("role", f"no role for {c.name!r}: add [role:narrator|singer|musician] to the artist "
                    f"line (performance_type {n.performance_type!r} has no default role)")
            continue
        if role not in AUDIO_ROLES:
            r.error("role", f"role {role!r} for {c.name!r} is not one of {sorted(AUDIO_ROLES)}")
            continue
        item = {"type": "person", "role": role}
        if c.op:
            item["id"] = c.op
        if c.bdrc:
            item["bdrc_id"] = c.bdrc
        out.append(item)
    if out and not any(c.role for c in n.contributors):
        r.note("role-default", f"role `{out[0]['role']}` comes from performance_type "
               f"{n.performance_type!r}; put [role:…] on the artist line to change it")
    return out


def check_root(n: an.AudioNote, r: Report):
    if not n.root_link:
        r.error("root-link", "root_text has no [[link]]")
        return
    if not n.root_note:
        r.error("root-missing", f"root_text `[[{n.root_link}]]` does not exist: fix the link to point "
                "at the root text in 1-SOURCES/Text")
        return
    if not n.text_id:
        r.error("text-id", f"{n.root_note.name} has no text_id: the audio has no text to attach to")
    if not n.edition_id:
        r.error("edition-id", f"{n.root_note.name} has no edition_id: the recording cannot follow an "
                "edition and no cue can be placed")
    if not n.root_blocks:
        r.warn("root-blocks", f"{n.root_note.name} has no block ids (^id): the Mapping table cannot be checked")


def check_recordings(n: an.AudioNote, r: Report, *, do_hash, require_file, seen_files):
    out = []
    if not n.recordings:
        r.error("recording-missing", "no recording described (audio_file / duration_seconds / "
                "sha256, or a `recordings:` list)")
        return out
    keys = [x.key for x in n.recordings]
    if len(set(keys)) != len(keys):
        r.error("recording-key", f"recordings share an id: {keys}")
    for rec in n.recordings:
        where = f"recording {rec.key!r}"
        meta = {"title": {"en": rec.key.replace("-", " ").capitalize()}}
        if n.edition_id:
            meta["edition_id"] = n.edition_id

        dur = rec.duration_seconds
        if dur in (None, ""):
            r.warn("duration", f"{where}: no duration_seconds, so duration_ms is not sent "
                   "(the server does not compute it)")
        elif not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur <= 0:
            r.error("duration", f"{where}: duration_seconds {dur!r} is not a positive number")
        else:
            meta["duration_ms"] = round(dur * 1000)

        if rec.size_bytes is not None and (not isinstance(rec.size_bytes, int) or rec.size_bytes <= 0):
            r.error("size", f"{where}: file_size_bytes {rec.size_bytes!r} is not a positive integer")
        if not rec.sha256:
            r.warn("sha256", f"{where}: no sha256, so the file cannot be checked before upload")
        elif not re.fullmatch(r"[0-9a-f]{64}", str(rec.sha256).lower()):
            r.error("sha256", f"{where}: sha256 {rec.sha256!r} is not 64 hex digits")

        declared_ext = Path(str(rec.declared)).suffix.lower() if rec.declared else ""
        if declared_ext and declared_ext not in an.AUDIO_EXT:
            r.error("format", f"{where}: {Path(str(rec.declared)).name} is not mp3/wav/m4a/ogg/flac")

        item = {"key": rec.key, "metadata": meta, "file": None}
        if rec.file is None:
            msg = (f"{where}: audio file not found (looked next to the note and in {an.FILES_ROOT}"
                   + (f"; several match: {[str(c) for c in rec.candidates]}" if rec.candidates else "")
                   + "). The files are not in the repo: download them and pass --audio-dir")
            (r.error if (require_file or rec.candidates) else r.warn)("file-missing", msg)
        else:
            ext = rec.file.suffix.lower()
            fmt, ctype = an.AUDIO_EXT[ext]
            size = rec.file.stat().st_size
            item.update(file=str(rec.file), content_type=ctype, format=fmt, size_bytes=size)
            if ext == ".mp4":
                r.note("mp4", f"{where}: {rec.file.name} is sent as audio/mp4 and stored as m4a")
            if declared_ext and declared_ext != ext:
                r.warn("file-name", f"{where}: note says {Path(str(rec.declared)).name}, found {rec.file.name}")
            if isinstance(rec.size_bytes, int) and rec.size_bytes != size:
                r.error("size", f"{where}: file is {size} bytes, note says {rec.size_bytes}")
            if rec.candidates:
                r.warn("file-duplicate", f"{where}: the same file is also at "
                       f"{[str(c.relative_to(an.FILES_ROOT)) if an.FILES_ROOT in c.parents else str(c) for c in rec.candidates]}")
            if do_hash:
                digest = an.sha256_of(rec.file)
                item["sha256"] = digest
                if rec.sha256 and digest != str(rec.sha256).lower():
                    r.error("sha256", f"{where}: file sha256 {digest[:12]}… ≠ note {str(rec.sha256)[:12]}…")
            other = seen_files.get(rec.file)
            if other and other != n.path:
                r.warn("file-shared", f"{where}: the same file is used by {other.relative_to(an.VAULT)}")
            seen_files.setdefault(rec.file, n.path)
        out.append(item)
    return out


def check_mapping(n: an.AudioNote, r: Report, *, live_host):
    """Check the Mapping table; -> list of cue sources {ref, start_ms, end_ms[, span]}."""
    otr = sorted((n.text_folder / "otr").glob("*.otr")) if (n.text_folder / "otr").is_dir() else []
    if n.mapping is None:
        r.warn("mapping-missing", "no `## Mapping` table")
        return []
    cols = n.mapping_columns
    for need in ("section", "start", "end"):
        if not any(c.startswith(need) for c in cols):
            r.error("mapping-columns", f"Mapping table has no {need.capitalize()} column (columns: {cols})")
            return []
    for p in n.problems:
        if p.startswith("Mapping"):
            r.error("mapping-time", p)

    durations = [x.duration_seconds for x in n.recordings if isinstance(x.duration_seconds, (int, float))]
    dur_ms = round(durations[0] * 1000) if len(durations) == 1 else None
    if len(n.recordings) > 1 and any(x.start_ms is not None for x in n.mapping):
        r.warn("mapping-recordings", "the note has several recordings but one Mapping table: its "
               "times are checked against none of them")

    order = {bid: i for i, bid in enumerate(n.root_blocks)}
    cues, prev, seen_ids, last_pos = [], None, {}, -1
    labelled = 0
    for row in n.mapping:
        at = f"Mapping line {row.line} (#{row.number or '?'})"
        sec = row.section.strip()
        timed = row.start_ms is not None or row.end_ms is not None
        bid = sec[1:] if sec.startswith("^") else None
        if bid is None:
            if timed:
                r.error("mapping-section", f"{at}: has times but Section {sec!r} is not a block id (^id)")
            else:
                labelled += 1
            continue
        if n.root_blocks and bid not in n.root_blocks:
            r.error("mapping-section", f"{at}: Section ^{bid} is not a block of {n.root_note.name}")
            continue
        if bid in seen_ids:
            r.warn("mapping-repeat", f"{at}: ^{bid} is also on line {seen_ids[bid]}")
        seen_ids.setdefault(bid, row.line)
        if n.root_blocks:
            pos = order[bid]
            if pos < last_pos:
                r.warn("mapping-order", f"{at}: ^{bid} comes before the previous row's block in the root text")
            last_pos = max(last_pos, pos)
            block = n.root_blocks[bid]
            shown = norm(row.text.replace("…", "").replace("...", ""))[:12]
            if shown and not norm(block["text"]).startswith(shown):
                r.warn("mapping-text", f"{at}: Text column does not start like ^{bid} "
                       f"({block['first'][:30]!r})")
        if not timed:
            continue
        if row.start_ms is None or row.end_ms is None:
            r.error("mapping-time", f"{at}: ^{bid} has only a {'Start' if row.start_ms is not None else 'End'}")
            continue
        if row.end_ms <= row.start_ms:
            r.error("mapping-time", f"{at}: ^{bid} ends ({row.end}) at or before it starts ({row.start})")
            continue
        if row.end_ms - row.start_ms < SHORT_CUE_MS:
            r.warn("mapping-short", f"{at}: ^{bid} lasts {row.end_ms - row.start_ms} ms "
                   f"({row.start}–{row.end}): two timestamps nearly on top of each other?")
        if dur_ms is not None and row.end_ms > dur_ms + DURATION_SLACK_MS:
            r.error("mapping-time", f"{at}: ^{bid} ends at {row.end}, after the recording ({dur_ms / 1000:.3f} s)")
        if prev is not None:
            if row.start_ms < prev.start_ms:
                r.error("mapping-time", f"{at}: ^{bid} starts ({row.start}) before the previous timed "
                        f"row (line {prev.line}, {prev.start})")
            elif row.start_ms < prev.end_ms:
                r.error("mapping-time", f"{at}: ^{bid} starts ({row.start}) before the previous timed "
                        f"row ends (line {prev.line}, {prev.end})")
        prev = row
        cues.append({"ref": bid, "start_ms": row.start_ms, "end_ms": row.end_ms, "line": row.line})

    if labelled:
        r.note("mapping-labels", f"{labelled} untimed row(s) have a label, not a block id, in Section "
               "(old layout): fine until they get times")
    if not cues:
        r.note("cues-none", "no timed rows: upload the audio and recording now, the cues later"
               + (f" ({otr[0].name} exists: run otr_to_mapping.py)" if otr else ""))
    elif otr:
        r.note("cues", f"{len(cues)} timed row(s), from {otr[0].name}")
    else:
        r.note("cues", f"{len(cues)} timed row(s)")

    if cues and live_host and n.edition_id:
        try:
            content, segs = live_segments(live_host, n.edition_id)
        except Exception as exc:          # noqa: BLE001
            r.error("live", f"cannot read edition {n.edition_id} on {live_host}: {exc}")
            return cues
        place_cues(n, r, cues, content, segs, live_host)
    return cues


def place_cues(n, r, cues, content, segs, host):
    """Give each cue the span of its block's text in the live edition content.

    Blocks are found by their text, in order (a refrain recited twice finds its
    second copy). When a live segment covers exactly that text, its span is used
    as is (it includes the trailing punctuation). Segment references are not
    relied on: in some editions they differ from the note's block ids.
    """
    keep = [i for i, ch in enumerate(content) if not NORM.match(ch)]
    flat = "".join(content[i] for i in keep)
    by_start = {}
    for ref, (a, z) in segs.items():
        by_start.setdefault(a, []).append((a, z, ref))
    ptr, partial = 0, []
    for c in cues:
        want = norm(n.root_blocks.get(c["ref"], {}).get("text", ""))
        at = flat.find(want, ptr) if want else -1
        if at < 0:
            r.error("cue-text", f"Mapping line {c['line']}: the text of ^{c['ref']} is not in edition "
                    f"{n.edition_id} on {host} (after the previous cue), so its cue has no span. "
                    "The live edition differs from the root text note")
            continue
        ptr = at + len(want)
        a, z = keep[at], keep[at + len(want) - 1] + 1
        seg = next(((sa, sz, ref) for sa, sz, ref in by_start.get(a, []) if norm(content[sa:sz]) == want), None)
        if seg:
            a, z = seg[0], seg[1]
        else:
            partial.append(c["ref"])
        c["span"] = {"start": a, "end": z}
    if partial:
        r.warn("cue-partial", f"{len(partial)} cue(s) cover text that is not a live segment of its own "
               f"(segments merged or split differently on {host}): "
               + ", ".join("^" + b for b in partial[:8]) + (" …" if len(partial) > 8 else ""))


# ------------------------------------------------------------------ main

def lint(n: an.AudioNote, *, live_host=None, do_hash=False, require_file=False, seen_files=None):
    r = Report()
    for p in n.problems:
        if not p.startswith("Mapping"):
            r.error("parse", p)
    check_metadata(n, r)
    contribs = contributions(n, r)
    check_root(n, r)
    recs = check_recordings(n, r, do_hash=do_hash, require_file=require_file,
                            seen_files=seen_files if seen_files is not None else {})
    cue_src = check_mapping(n, r, live_host=live_host)
    if n.dev_ids.get("dev_audio_id"):
        r.note("uploaded", f"already on dev: audio {n.dev_ids['dev_audio_id']}, "
               f"recording {n.dev_ids.get('dev_recording_id')}")

    audio_input = {}
    if n.title and "?" not in n.title:
        audio_input["title"] = n.title
    if n.language:
        audio_input["language"] = n.language
    if n.license:
        audio_input["license"] = n.license
    audio_input["contributions"] = contribs
    cues = ([{"span": c["span"], "start_ms": c["start_ms"], "end_ms": c["end_ms"]} for c in cue_src if "span" in c]
            if live_host else None)
    return {
        "note": str(n.path.relative_to(an.VAULT)),
        "stem": n.stem,
        "root_text": str(n.root_note.relative_to(an.VAULT)) if n.root_note else None,
        "text_id": n.text_id,
        "edition_id": n.edition_id,
        "audio_input": audio_input,
        "recordings": recs,
        "cue_sources": [{k: c[k] for k in ("ref", "start_ms", "end_ms", "line")} for c in cue_src],
        "cues": cues,
        "errors": r.errors,
        "warnings": r.warnings,
        "notes": r.notes,
    }


def write_output(res):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    name = res["stem"].replace("/", "_").replace("\\", "_")
    ok = OUTPUT_DIR / f"{name}.lint.json"
    bad = OUTPUT_DIR / f"{name}.lint.errors.json"
    target, stale = (bad, ok) if res["errors"] else (ok, bad)
    if stale.exists():
        stale.unlink()
    res = dict(res, linted_at=datetime.now(timezone.utc).isoformat())
    target.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


DEFAULT_REPORT = an.VAULT / "4-SYSTEM" / "audio-lint-report.md"


def _note_link(rel):
    """Obsidian link to a note, shown as its text folder name."""
    p = Path(rel)
    folder = p.parent.parent.name if p.parent.name == "audio" else p.parent.name
    return f"[[{p.with_suffix('').as_posix()}|{folder}]]"


def write_report(path, results, *, title, run_info, sections=()):
    """Markdown report: which notes failed and why, then warnings by kind.

    results  -- lint() results (dicts with note/errors/warnings)
    sections -- extra (heading, [lines]) blocks, e.g. upload outcomes
    """
    failed = [r for r in results if r["errors"]]
    warned = [r for r in results if not r["errors"] and r["warnings"]]
    clean = len(results) - len(failed) - len(warned)
    codes = {}
    for r in failed:
        for code in {m["code"] for m in r["errors"]}:
            codes[code] = codes.get(code, 0) + 1
    out = [f"# {title}", "",
           f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `{run_info}`.", "",
           f"**{len(results)} notes: {len(failed)} failed, {len(warned)} passed with warnings, "
           f"{clean} clean.** A failed note is not uploaded; warnings do not block. "
           "Rules: `4-SYSTEM/Skills/library-upload/scripts/linter-audio/README.md`.", ""]
    for heading, lines in sections:
        out += [f"## {heading}", ""] + list(lines) + [""]
    if failed:
        out += [f"## Failed ({len(failed)})", "", "| Error | Notes |", "| --- | --- |"]
        out += [f"| `{c}` | {k} |" for c, k in sorted(codes.items(), key=lambda x: (-x[1], x[0]))]
        out.append("")
        for r in sorted(failed, key=lambda r: r["note"]):
            out += [f"### {_note_link(r['note'])}", ""]
            out += [f"- **`{m['code']}`** {m['message']}" for m in r["errors"]]
            out.append("")
    if warned or any(r["warnings"] for r in failed):
        by_code = {}
        for r in results:
            for m in r["warnings"]:
                by_code.setdefault(m["code"], []).append((r["note"], m["message"]))
        out += [f"## Warnings", ""]
        for code, items in sorted(by_code.items()):
            out += [f"### `{code}` ({len({n for n, _ in items})} notes)", ""]
            out += [f"- {_note_link(n)}: {msg}" for n, msg in items]
            out.append("")
    Path(path).write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def collect(paths):
    notes = []
    for p in (paths or [an.NOTES_ROOT]):
        p = Path(p).resolve()
        if p.is_dir():
            notes += sorted(x for x in p.rglob("*.md")
                            if not x.name.startswith("._") and "__MACOSX" not in x.parts)
        else:
            notes.append(p)
    return notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--host", choices=sorted(HOSTS), default="dev")
    ap.add_argument("--hash", action="store_true")
    ap.add_argument("--audio-dir")
    ap.add_argument("--require-file", action="store_true")
    ap.add_argument("--no-output", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true", help="print only notes with errors, and the summary")
    ap.add_argument("--report", nargs="?", const=str(DEFAULT_REPORT), metavar="FILE",
                    help=f"write a Markdown report of what failed and why (default file: "
                         f"{DEFAULT_REPORT.relative_to(an.VAULT)})")
    args = ap.parse_args(argv)
    if args.audio_dir:
        an.set_files_root(args.audio_dir)

    seen, totals, n_err, results = {}, {}, 0, []
    for path in collect(args.paths):
        try:
            n = an.parse_note(path)
        except Exception as exc:          # noqa: BLE001
            n_err += 1
            print(f"ERROR  {path}\n         cannot parse: {exc}")
            results.append({"note": str(path.relative_to(an.VAULT)), "warnings": [], "notes": [],
                            "errors": [{"code": "parse", "message": f"cannot parse: {exc}"}]})
            continue
        res = lint(n, live_host=args.host if args.live else None, do_hash=args.hash,
                   require_file=args.require_file, seen_files=seen)
        results.append(res)
        if not args.no_output:
            write_output(res)
        for key in {(kind, m["code"]) for kind in ("errors", "warnings") for m in res[kind]}:
            totals[key] = totals.get(key, 0) + 1
        n_err += bool(res["errors"])
        status = "ERROR" if res["errors"] else "warn " if res["warnings"] else "ok   "
        if args.quiet and not res["errors"]:
            continue
        print(f"{status}  {res['note']}")
        for kind, mark in (("errors", "E"), ("warnings", "W"), ("notes", "-")):
            if args.quiet and kind != "errors":
                continue
            for m in res[kind]:
                print(f"    {mark} [{m['code']}] {m['message']}")

    print("\nsummary (notes affected):")
    for (kind, code), count in sorted(totals.items()):
        print(f"  {kind[:-1]:7} {code:20} {count}")
    print(f"{n_err} note(s) with errors")
    if args.report:
        flags = " ".join(a for a in (argv if argv is not None else sys.argv[1:]))
        write_report(args.report, results, title="Audio lint report",
                     run_info=f"lint_audio.py {flags}".strip())
        print(f"report: {args.report}")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
