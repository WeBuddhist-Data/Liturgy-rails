"""Build a note's payloads with the vault's OWN shared linter and parser.

    text / translation  -> 4-SYSTEM/scripts/linter-root-text  + parser-root-text
    commentary          -> 4-SYSTEM/scripts/linter-commentary + parser-commentary

These are the tools every rails vault keeps in step with rails-template; using them
(rather than a copy bundled in the skill) means what is uploaded is exactly what the
vault's linter checked and its parser produced: text, edition (content + segmentation),
table of contents, yigchungs, durchens, footnotes and, for a translation or commentary,
the alignment to its root.

The linters fill in some frontmatter in place (language name, lang_tag, translation_of /
commentary_of, category_id). A read-only command must not change notes, so the note is
put back byte for byte afterwards unless `keep_edits` (a real upload) is set.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from .vault import TOOLS, VAULT

TOOLSETS = {
    "root": ("linter-root-text", "parser-root-text"),
    "commentary": ("linter-commentary", "parser-commentary"),
}


class BuildError(Exception):
    pass


def _tools(kind):
    lint_dir, parse_dir = TOOLSETS["commentary" if kind == "commentary" else "root"]
    linter, parser = TOOLS / lint_dir / "lint_text_input.py", TOOLS / parse_dir / "parser.py"
    for t in (linter, parser):
        if not t.is_file():
            raise BuildError(f"{t.relative_to(VAULT) if VAULT in t.parents else t} not found: this vault needs "
                             "rails-template's shared linter and parser (4-SYSTEM/scripts/)")
    return linter, parser


def _find(folder: Path, name: str):
    p = folder / name
    if p.exists():
        return p
    want = unicodedata.normalize("NFC", name)
    if folder.is_dir():
        for q in folder.iterdir():
            if unicodedata.normalize("NFC", q.name) == want:
                return q
    return None


def _load(folder, name, key=None, default=None):
    p = _find(folder, name) if folder else None
    if not p:
        return default
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get(key, default) if key else data


def build(path, kind, keep_edits=False) -> dict:
    """-> {text, edition, toc (sections), yigchungs, durchens, footnotes, alignment (pairs), lint}."""
    path = Path(path).resolve()
    linter, parser = _tools(kind)
    stem = path.stem
    snapshot = path.read_bytes()
    try:
        r = subprocess.run([sys.executable, str(linter), str(path)], cwd=VAULT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        lint_out = linter.parent / "output"
        ok = _find(lint_out, f"{stem}.lint.json")
        bad = _find(lint_out, f"{stem}.lint.errors.json")
        if r.returncode != 0 or not ok or (bad and bad.stat().st_mtime > ok.stat().st_mtime):
            errors = _load(lint_out, f"{stem}.lint.errors.json", "errors") or [
                (r.stderr or r.stdout).strip()[-400:] or "the linter failed"]
            raise BuildError("lint: " + "; ".join(str(e) for e in errors[:6]))
        out = parser.parent / "output"
        old = _find(out, stem)
        if old and old.is_dir():
            shutil.rmtree(old)                  # no stale alignment/yigchung files from an earlier run
        p = subprocess.run([sys.executable, str(parser), str(path), str(ok)], cwd=VAULT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        folder = _find(out, stem)
        if p.returncode != 0 or not folder:
            raise BuildError("parse: " + ((p.stderr or p.stdout).strip()[-400:] or "the parser failed"))
        edition = _load(folder, f"{stem}.edition.json")
        if not edition:
            raise BuildError("parse: no edition.json")
        return {
            "text": _load(folder, f"{stem}.text.json", default={}),
            "edition": edition,
            "toc": _load(folder, f"{stem}.toc.json", "sections", []),
            "yigchungs": _load(folder, f"{stem}.yigchungs.json", "yigchungs", []),
            "durchens": _load(folder, f"{stem}.durchens.json", "durchens", []),
            "footnotes": _load(folder, f"{stem}.footnotes.json", "footnotes", []),
            "alignment": _load(folder, f"{stem}.alignment.json", "alignments", None),
            "lint": json.loads(ok.read_text(encoding="utf-8")),
        }
    finally:
        if not keep_edits and path.read_bytes() != snapshot:
            path.write_bytes(snapshot)
