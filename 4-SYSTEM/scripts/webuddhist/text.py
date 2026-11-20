"""Texts, translations and commentaries on one library environment.

  text         root texts           1-SOURCES/Text/*.md
  translation  translations         3-TRANSFORMATIONS/Translations/<track>/<lang>/<root>-<lang>.md
               (`translation_of: 1-SOURCES/Text/<root>.md`), aligned root -> translation,
               block N with root block N by position
  commentary   commentaries         1-SOURCES/Commentary/**/*.md (or any path given)
               (`file_type: commentary`, `commentary_of: [[1-SOURCES/Text/<root>]]`), aligned
               commentary -> root from the commentary's transclusions `![[…/<root>#^id]]`:
               every root block a commentary block transcludes becomes a pair (many-to-many,
               as the Heart Sutra commentaries on the library are). The transclusions are
               taken out of the uploaded content.

Each note is built into the payloads the library takes (the builders the original
uploads used: liturgy_payloads + the vendored parser) and compared with what the
environment holds. States:

  ok                  text, edition content, segmentation (and alignment) match the note
  new                 not on this environment                              -> push
  details-changed     title / license / category / bdrc / date / authors differ -> update --only details
  content-patchable   content differs, and patching it in place keeps every
                      segment boundary (proven by replaying the server's span
                      arithmetic)                                          -> update --only content
  resegment-needed    content and segment boundaries/types differ (a split,
                      merge or retype): only a re-segmentation can do it   -> replace  (gated)
  refs-renumbered     content and spans identical, only the segment references
                      differ (the vault's block ids were renumbered after upload) -> replace (gated)
  alignment-missing   no alignment with its root on this environment       -> push --only alignment
  alignment-differs   the alignment pairs differ from the note's            -> push --only alignment
  alignment-blocked   no alignment can be built (block counts differ, a transclusion points
                      at a block the root does not have)
  root-not-on-env     its root text is not on this environment
  link-differs        translation_of / commentary_of points at another root; cannot be patched
  edition-mismatch    the ledger's edition is not one of the text's editions -> verify
  gone                the ledger names a text this environment does not have
  blocked             the note cannot be built (reason quoted)

Re-segmentation (`replace`) always runs one fixed sequence on the edition:
DELETE segmentation -> PATCH content (one REPLACE of the whole content, only if it
changed) -> POST segmentation, each step checked before the next. If the text is
linked to anything (translations, commentaries, alignments, recordings, or it is
itself a translation/commentary), `replace` refuses unless --dev is given:
re-segmenting breaks those links and only a developer knows what has to be redone.
Texts are never deleted.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .api import ApiError, items
from .envs import VAULT
from .plan import NotePlan, Step

SCRIPTS = VAULT / "4-SYSTEM" / "scripts"
ROOT_DIR = VAULT / "1-SOURCES" / "Text"
TR_DIR = VAULT / "3-TRANSFORMATIONS" / "Translations"
COM_DIR = VAULT / "1-SOURCES" / "Commentary"
DEFAULT_DIR = {"text": ROOT_DIR, "translation": TR_DIR, "commentary": COM_DIR}
LINKED_KINDS = ("translation", "commentary")
EDITION_TYPES = {"diplomatic", "critical", "collated"}

EMBED_RE = re.compile(r"!\[\[([^\]#|]+)#\^([\w-]+)(?:\|[^\]]*)?\]\]")
BLOCK_ID_RE = re.compile(r"\s\^([\w-]+)\s*$")
LINK_RE = re.compile(r"\[\[([^\]|#]+)")

FIX = {
    "new": "push", "details-changed": "update --only details", "content-patchable": "update --only content",
    "resegment-needed": "replace (developer only if linked)", "refs-renumbered": "replace (developer only if linked)",
    "alignment-missing": "push --only alignment", "alignment-differs": "push --only alignment",
    "alignment-blocked": "fix the note (block counts / transclusions) or re-segment the root first",
    "root-not-on-env": "push the root text to this environment first",
    "link-differs": "cannot be patched: ask a developer", "edition-mismatch": "check by hand (verify)",
    "gone": "check by hand (verify)", "blocked": "fix the note",
}
NO_WRITE = ("blocked", "gone", "edition-mismatch", "link-differs")


# ------------------------------------------------------------------ builders

_MODS = {}


def _load(name, path):
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(path).parent))
        spec.loader.exec_module(mod)
        _MODS[name] = mod
    return _MODS[name]


def LP():
    return _load("liturgy_payloads", SCRIPTS / "liturgy_payloads.py")


def P():
    if "parser" not in _MODS:
        _MODS["parser"] = LP().load_upstream_parser()
    return _MODS["parser"]


def CP():
    return _load("content_patch_payloads", SCRIPTS / "content_patch_payloads.py")


def _edition(path, fm, default_type="critical", retype=True):
    """The edition payload exactly as the original uploaders built it: root texts get
    shape-derived segment types (liturgy_payloads), translations keep the parser's
    (translation_payloads never retyped)."""
    with contextlib.redirect_stdout(io.StringIO()):       # the parser WARNs on unmarked quote blocks
        ed = P().build_edition(Path(path), None)[1]
    if retype:
        LP().retype_segments(ed)
    etype = (fm.get("edition_type") or default_type).strip()
    if etype not in EDITION_TYPES:
        raise ValueError(f"edition_type {etype!r} not one of {sorted(EDITION_TYPES)}")
    meta = {"type": etype}
    if (fm.get("source") or "").strip():
        meta["source"] = fm["source"].strip()
    segs = [{"lines": s["lines"], "type": s["type"], "reference": str(s["reference"])}
            for s in ed["segmentation"]["segments"]]
    for s in segs:
        for sp in s["lines"]:
            if not 0 <= sp["start"] <= sp["end"] <= len(ed["content"]):
                raise ValueError(f"segment {s['reference']} span out of range")
    return {"metadata": meta, "content": ed["content"], "segmentation": {"segments": segs}}


def _root_key_of(value) -> str | None:
    """`commentary_of` / `translation_of` (a [[link]] or a path) -> the root note's stem."""
    s = str(value or "").strip()
    m = LINK_RE.search(s)
    s = m.group(1) if m else s
    return Path(s.strip()).stem if s else None


def split_embeds(raw: str):
    """Take the root transclusions out of a commentary note.

    -> (note text without the embeds, {commentary block id: [(note path, root block id), ...]},
        [embeds after the last block]).
    An embed belongs to the block it is in; an embed-only block belongs to the next block
    that has an id (the transclusion placed just above the comment it introduces)."""
    if not raw.startswith("---"):
        return raw, {}, []
    end = raw.find("\n---", 3)
    head, body = raw[:end + 4], raw[end + 4:]
    out, links, pending = [], {}, []
    for block in re.split(r"\n[ \t]*\n", body):
        found = EMBED_RE.findall(block)
        if not found:
            out.append(block)
            lines = [l for l in block.split("\n") if l.strip()]
            m = BLOCK_ID_RE.search(lines[-1]) if lines else None
            if m and pending:
                links.setdefault(m.group(1), []).extend(pending)
                pending = []
            continue
        kept = [EMBED_RE.sub("", l).rstrip() for l in block.split("\n")]
        kept = [l for l, orig in zip(kept, block.split("\n")) if l.strip() or not orig.strip()]
        if not any(l.strip() for l in kept):
            pending += found
            continue
        out.append("\n".join(kept))
        lines = [l for l in kept if l.strip()]
        m = BLOCK_ID_RE.search(lines[-1])
        if m:
            links.setdefault(m.group(1), []).extend(pending + found)
            pending = []
        else:
            pending += found
    return head + "\n\n".join(out), links, pending


# ------------------------------------------------------------------ items

@dataclass
class Item:
    kind: str                         # "text" | "translation" | "commentary"
    path: Path
    key: str                          # ledger key: <stem>, <stem>-<lang>, commentary/<stem>
    root_key: str | None = None
    fm: dict = field(default_factory=dict)
    text: dict | None = None          # TextInput
    edition: dict | None = None       # EditionRequestModel
    pairs: list | None = None         # alignment pairs on this environment
    embeds: dict | None = None        # commentary: block id -> [root note block ids]
    notes: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    text_id: str | None = None
    edition_id: str | None = None
    id_source: str | None = None      # "ledger" | "note"
    root_ids: tuple | None = None     # (text_id, edition_id) of the root on this env
    root_live_refs: list | None = None  # the root edition's segment references on this env
    live_text: dict | None = None
    live_content: str | None = None
    live_segs: list | None = None
    live_pairs: list | None = None
    ops: list | None = None

    @property
    def rel(self):
        try:
            return self.path.relative_to(VAULT)
        except ValueError:
            return self.path

    @property
    def link(self):
        return f"[[{self.rel.with_suffix('').as_posix()}|{self.key}]]"

    @property
    def title(self):
        return self.key

    @property
    def link_field(self):
        return {"translation": "translation_of", "commentary": "commentary_of"}.get(self.kind)

    def align_ends(self):
        """(source edition, target edition) of this note's alignment: translations are stored
        root -> translation, commentaries commentary -> root (as on the library)."""
        root = self.root_ids[1] if self.root_ids else None
        return (root, self.edition_id) if self.kind == "translation" else (self.edition_id, root)


def collect(kind, paths):
    roots = [Path(p).resolve() for p in paths] if paths else [DEFAULT_DIR[kind]]
    out = []
    for r in roots:
        if not r.exists():
            continue
        files = sorted(r.rglob("*.md")) if r.is_dir() else [r]
        out += [f for f in files if not f.name.startswith(("._", "About "))]
    return out


_ROOT_REFS = {}     # root stem -> its note's block ids, built once per run


def _root_refs(root_key):
    if root_key not in _ROOT_REFS:
        root_path = ROOT_DIR / f"{root_key}.md"
        root_fm = P()._read_source(root_path)[0]
        _ROOT_REFS[root_key] = [s["reference"] for s in _edition(root_path, root_fm)["segmentation"]["segments"]]
    return _ROOT_REFS[root_key]


def build(kind, path) -> Item | None:
    """-> the built Item, or None for a file that is not a note of this kind (about.md, headers)."""
    path = Path(path)
    it = Item(kind=kind, path=path, key=("commentary/" if kind == "commentary" else "") + path.stem)
    try:
        it.fm = fm = P()._read_source(path)[0]
    except Exception as exc:  # noqa: BLE001
        if kind in LINKED_KINDS:
            return None
        it.flags["blocked"] = f"cannot read the note: {exc}"
        return it
    if kind in LINKED_KINDS and str(fm.get("file_type") or "").strip() != kind:
        return None
    try:
        if kind == "text":
            it.text = LP().build_text(fm, str(fm.get("category_id") or "").strip() or None, path)
            if it.text.get("category_id") is None:
                it.text.pop("category_id")
            it.edition = _edition(path, fm)
        elif kind == "translation":
            _build_translation(it, fm)
        else:
            _build_commentary(it, fm)
    except Exception as exc:  # noqa: BLE001
        it.flags["blocked"] = f"{type(exc).__name__}: {exc}"
    return it


def _root_fm(it):
    root_path = ROOT_DIR / f"{it.root_key}.md"
    if not root_path.is_file():
        raise ValueError(f"root note {root_path.name} not found")
    return P()._read_source(root_path)[0]


def _build_translation(it, fm):
    path = it.path
    lang = str(fm.get("lang_tag") or "").strip()
    if not lang or not path.stem.endswith(f"-{lang}"):
        raise ValueError(f"file name does not end in -{lang or '<lang_tag>'}")
    it.root_key = path.stem[: -(len(lang) + 1)]
    title = str(fm.get("title_translated") or "").strip()
    if not title:
        raise ValueError("title_translated missing (the `title` carries the track name)")
    it.text = {"title": {lang: title}, "language": lang}
    root_fm = _root_fm(it)
    cat = str(fm.get("category_id") or root_fm.get("category_id") or "").strip()
    if cat:
        it.text["category_id"] = cat
    lic = str(fm.get("license") or "").strip()
    if lic:
        it.text["license"] = lic
    it.edition = _edition(path, fm, retype=False)
    if not it.edition["content"].strip():
        raise ValueError("content is empty")
    n_src, n_tgt = len(_root_refs(it.root_key)), len(it.edition["segmentation"]["segments"])
    if n_src != n_tgt:
        raise ValueError(f"{n_tgt} blocks, the root note has {n_src}: block N must render root block N, "
                         "so no alignment can be built")


def _build_commentary(it, fm):
    it.root_key = _root_key_of(fm.get("commentary_of"))
    if not it.root_key:
        raise ValueError("commentary_of missing: link the root text, e.g. commentary_of: \"[[1-SOURCES/Text/<root>]]\"")
    root_fm = _root_fm(it)
    raw = it.path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    clean, links, trailing = split_embeds(raw)
    if trailing:
        raise ValueError(f"{len(trailing)} transclusion(s) after the last block: they belong to no comment")
    it.embeds, foreign = {}, 0
    for block, targets in links.items():
        for note, rid in targets:
            if Path(note.strip()).stem == it.root_key:
                it.embeds.setdefault(block, []).append(rid)
            else:
                foreign += 1
    if foreign:
        it.notes.append(f"{foreign} transclusion(s) of other notes are not alignment pairs")
    if not it.embeds:
        raise ValueError(f"no transclusions of the root ![[…/{it.root_key}#^id]]: nothing to align")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / it.path.name
        tmp.write_text(clean, encoding="utf-8")
        it.text = LP().build_text(fm, str(fm.get("category_id") or root_fm.get("category_id") or "").strip()
                                  or None, tmp)
        if it.text.get("category_id") is None:
            it.text.pop("category_id")
        it.edition = _edition(tmp, fm)
    if "![[" in it.edition["content"]:
        raise ValueError("a transclusion is still in the content (an embed inside a sentence?)")
    own = {s["reference"] for s in it.edition["segmentation"]["segments"]}
    missing = sorted(set(it.embeds) - own)
    if missing:
        raise ValueError(f"transclusions attached to blocks that are not segments: {missing[:5]}")
    root_refs = set(_root_refs(it.root_key))
    bad = sorted({r for rs in it.embeds.values() for r in rs} - root_refs)
    if bad:
        raise ValueError(f"transcludes root block(s) the root note does not have: {', '.join('^' + b for b in bad[:6])}")


# ------------------------------------------------------------------ live reads

@dataclass
class Ctx:
    env: object
    client: object
    ledger: object
    dev: bool = False


def _segments(client, eid):
    out, off = [], 0
    while True:
        page = client.get(f"/v2/editions/{eid}/segmentation/segments?limit=200&offset={off}")
        out += page.get("items", [])
        if not page.get("has_more") or not page.get("items"):
            return out
        off += len(page["items"])


def _pairs(client, src, tgt):
    out, off = [], 0
    while True:
        try:
            page = client.get(f"/v2/editions/{src}/alignments/{tgt}?limit=200&offset={off}")
        except ApiError as exc:
            if exc.status in (404, 422):
                return []
            raise
        got = items(page)
        out += [(str(x["source_segment"]["reference"]), str(x["target_segment"]["reference"])) for x in got]
        if not (isinstance(page, dict) and page.get("has_more")) or not got:
            return out
        off += len(got)


def _resolve(it: Item, ctx: Ctx):
    """Ids of this note on this environment: the env's ledger, else (root texts and commentaries)
    the note's own ids if this environment has them (dev was replicated from prod once)."""
    e = ctx.ledger.get(it.key) or {}
    if e.get("text_id"):
        it.text_id, it.edition_id, it.id_source = e["text_id"], e.get("edition_id"), "ledger"
    elif it.kind in ("text", "commentary") and it.fm.get("text_id"):
        it.text_id, it.edition_id, it.id_source = str(it.fm["text_id"]), str(it.fm.get("edition_id") or ""), "note"
    if it.kind in LINKED_KINDS and it.root_key:
        r = ctx.ledger.get(it.root_key) or {}
        if r.get("text_id"):
            it.root_ids = (r["text_id"], r.get("edition_id"))


_ROOT_LIVE = {}     # root edition id -> its live segment references (one fetch per run)


def _fetch(it: Item, ctx: Ctx):
    c = ctx.client
    if it.kind in LINKED_KINDS and it.root_ids:
        if c.get_or_none(f"/v2/texts/{it.root_ids[0]}") is None:
            it.root_ids = None
        elif it.root_ids[1]:
            ed = it.root_ids[1]
            if ed not in _ROOT_LIVE:
                try:
                    _ROOT_LIVE[ed] = [str(s.get("reference")) for s in _segments(c, ed)]
                except ApiError:
                    _ROOT_LIVE[ed] = None
            it.root_live_refs = _ROOT_LIVE[ed]
    if it.text_id:
        it.live_text = c.get_or_none(f"/v2/texts/{it.text_id}")
        if it.live_text and it.edition_id and it.edition_id in (it.live_text.get("editions") or []):
            it.live_content = c.get(f"/v2/editions/{it.edition_id}/content")
            it.live_segs = _segments(c, it.edition_id)
            if it.kind in LINKED_KINDS and it.root_ids and it.root_ids[1]:
                it.live_pairs = _pairs(c, *it.align_ends())


def _shape(segs):
    return ([str(s.get("reference")) for s in segs], [[(l["start"], l["end"]) for l in s["lines"]] for s in segs],
            [s.get("type") for s in segs])


def _compare_edition(it: Item):
    """-> (state, detail) for the edition: ok / refs-renumbered / content-patchable / resegment-needed."""
    new = it.edition
    b_refs, b_spans, b_types = _shape(new["segmentation"]["segments"])
    l_refs, l_spans, l_types = _shape(it.live_segs or [])
    renum = b_refs != l_refs
    if new["content"] == it.live_content:
        if b_spans == l_spans and b_types == l_types:
            return ("refs-renumbered", _refs_detail(l_refs, b_refs)) if renum else ("ok", "")
        why = (f"{len(l_spans)} -> {len(b_spans)} segments" if len(b_spans) != len(l_spans)
               else "segment boundaries differ" if b_spans != l_spans else "segment types differ")
        return "resegment-needed", f"same text, {why}"
    cp = CP()
    ops = cp.make_ops(it.live_content, new["content"])
    text = it.live_content
    flat = [sp for s in l_spans for sp in s]
    owner = [i for i, s in enumerate(l_spans) for _ in s]
    for op in ops:
        flat = cp.apply_op_to_spans(flat, op)
        text = cp.apply_op_to_text(text, op)
    grouped = {}
    for sp, i in zip(flat, owner):
        if sp is not None:
            grouped.setdefault(i, []).append(sp)
    replayed = [grouped[i] for i in sorted(grouped)]
    if text == new["content"] and replayed == b_spans and b_types == l_types:
        it.ops = ops
        extra = f"; segment references also differ ({_refs_detail(l_refs, b_refs)})" if renum else ""
        return "content-patchable", f"{len(ops)} edit(s) in place, every segment boundary kept{extra}"
    why = ("content edits move segment boundaries" if text == new["content"] and replayed != b_spans
           else "segment types differ" if b_types != l_types else "content cannot be patched in place")
    return "resegment-needed", f"{why} ({len(l_spans)} -> {len(b_spans)} segments)"


def _refs_detail(live, built):
    d = [(a, b) for a, b in zip(live, built) if a != b]
    head = ", ".join(f"{a}→{b}" for a, b in d[:3])
    return f"{len(d)} reference(s) differ, e.g. {head}" if d else "segment count differs"


def _details_diff(it: Item) -> dict:
    live, want = it.live_text or {}, it.text or {}
    out = {}
    for k, v in want.items():
        lv = live.get(k)
        if k == "contributions":
            norm = lambda cs: sorted((c.get("type"), c.get("id"), c.get("role")) for c in cs or [])  # noqa: E731
            if norm(v) != norm(lv):
                out[k] = v
        elif v != lv:
            out[k] = v
    return out


def _make_pairs(it: Item, ctx: Ctx):
    """Alignment pairs as references that exist on this environment (the vault renumbered the roots'
    block ids after upload, so a root block is found by its position in the root note)."""
    f = it.flags
    if it.root_live_refs is None:
        f["alignment-blocked"] = f"the root's edition cannot be read on {ctx.env.name}"
        return
    note_refs = _root_refs(it.root_key)
    if len(it.root_live_refs) != len(note_refs):
        f["alignment-blocked"] = (f"the root has {len(it.root_live_refs)} segments on {ctx.env.name}, "
                                  f"{len(note_refs)} in its note: re-segment the root first")
        return
    to_live = dict(zip(note_refs, it.root_live_refs))
    if it.kind == "translation":
        tr_refs = [s["reference"] for s in it.edition["segmentation"]["segments"]]
        it.pairs = [{"source_segment_reference": a, "target_segment_reference": b}
                    for a, b in zip(it.root_live_refs, tr_refs)]
    else:
        it.pairs = [{"source_segment_reference": block, "target_segment_reference": to_live[r]}
                    for block, rs in it.embeds.items() for r in dict.fromkeys(rs)]


def assess_all(kind, paths, ctx: Ctx, progress=True) -> list:
    files = collect(kind, paths)
    built = []
    for i, p in enumerate(files, 1):
        if progress and sys.stderr.isatty():
            print(f"\r  building {i}/{len(files)} …", end="", file=sys.stderr, flush=True)
        it = build(kind, p)
        if it is not None:
            built.append(it)
    for it in built:
        _resolve(it, ctx)

    def fetch(it):
        try:
            _fetch(it, ctx)
        except ApiError as exc:
            it.flags["gone"] = f"cannot read from {ctx.env.name}: {exc}"
        return it

    done = 0
    with ThreadPoolExecutor(8) as ex:
        for _ in ex.map(fetch, built):
            done += 1
            if progress and sys.stderr.isatty():
                print(f"\r  reading {done}/{len(built)} on {ctx.env.name} …   ", end="", file=sys.stderr, flush=True)
    if progress and sys.stderr.isatty():
        print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    for it in built:
        _classify(it, ctx)
    return built


def _classify(it: Item, ctx: Ctx):
    f = it.flags
    if "blocked" in f or "gone" in f:
        return
    if it.kind in LINKED_KINDS:
        if not it.root_ids:
            f["root-not-on-env"] = f"root text {it.root_key} is not on {ctx.env.name} (or not in its ledger)"
            return
        _make_pairs(it, ctx)
    if not it.text_id:
        f["new"] = f"not on {ctx.env.name}"
        return
    if it.live_text is None:
        if it.id_source == "note":
            f["new"] = f"the note's text {it.text_id} is not on {ctx.env.name}"
        else:
            f["gone"] = f"text {it.text_id} is in the ledger but not on {ctx.env.name}"
        return
    if it.link_field and it.live_text.get(it.link_field) != it.root_ids[0]:
        f["link-differs"] = (f"{it.link_field} is {it.live_text.get(it.link_field)}, the root is {it.root_ids[0]} "
                             "(set only at creation)")
    if it.edition_id not in (it.live_text.get("editions") or []):
        f["edition-mismatch"] = (f"edition {it.edition_id} is not among the text's editions "
                                 f"{it.live_text.get('editions')}")
        return
    diff = _details_diff(it)
    if diff:
        f["details-changed"] = "differs: " + ", ".join(diff)
    state, detail = _compare_edition(it)
    if state != "ok":
        f[state] = detail
    if it.kind in LINKED_KINDS and it.pairs is not None:
        want = {(p["source_segment_reference"], p["target_segment_reference"]) for p in it.pairs}
        have = set(it.live_pairs or [])
        if not have:
            f["alignment-missing"] = f"no alignment with the root on {ctx.env.name}"
        elif want != have:
            f["alignment-differs"] = f"{len(have)} pairs on {ctx.env.name}, {len(want)} from the note"
    if not f:
        f["ok"] = ""


# ------------------------------------------------------------------ links (the re-segmentation gate)

def _vault_dependents(root_key) -> list:
    out = sorted(p.parent.name for p in TR_DIR.rglob(f"{root_key}-*.md"))
    if COM_DIR.is_dir():
        for p in COM_DIR.rglob("*.md"):
            head = p.read_text(encoding="utf-8", errors="replace")[:3000]
            m = re.search(r"^commentary_of:\s*(.+)$", head, re.M)
            if m and _root_key_of(m.group(1)) == root_key:
                out.append(f"commentary {p.stem}")
    return out


def links(it: Item, ctx: Ctx) -> list:
    """Everything a re-segmentation of this edition would break, read live."""
    out, t = [], it.live_text or {}
    if t.get("translation_of"):
        out.append(f"it is a translation of text {t['translation_of']}")
    if t.get("commentary_of"):
        out.append(f"it is a commentary on text {t['commentary_of']}")
    if t.get("translations"):
        out.append(f"{len(t['translations'])} translation(s): {', '.join(t['translations'][:8])}")
    if t.get("commentaries"):
        out.append(f"{len(t['commentaries'])} commentar(y/ies): {', '.join(t['commentaries'][:8])}")
    if it.edition_id:
        al = items(ctx.client.get(f"/v2/editions/{it.edition_id}/alignments"))
        if al:
            out.append(f"{len(al)} alignment(s) with other editions")
        recs = items(ctx.client.get(f"/v2/editions/{it.edition_id}/recordings"))
        with_cues = [r["id"] for r in recs if items(ctx.client.get(f"/v2/recordings/{r['id']}/cues"))]
        if with_cues:
            out.append(f"{len(with_cues)} recording(s) with cues: {', '.join(with_cues)}")
        elif recs:
            out.append(f"{len(recs)} recording(s) follow this edition (no cues)")
    if it.kind == "text":
        dep = _vault_dependents(it.key)
        if dep:
            out.append(f"notes in the vault built on it: {', '.join(dep)}")
    return out


# ------------------------------------------------------------------ steps

def _save(it: Item, ctx: Ctx, **kw):
    e = ctx.ledger.entry(it.key)
    e.update(kw)
    ctx.ledger.save()
    return e


def _post_text(it, ctx):
    body = dict(it.text)
    if it.link_field:
        body[it.link_field] = it.root_ids[0]
    out = ctx.client.post("/v2/texts", body)
    it.text_id = out["id"]
    _save(it, ctx, text_id=out["id"], note=str(it.rel))
    return f"text {out['id']}"


def _post_edition(it, ctx):
    out = ctx.client.post(f"/v2/texts/{it.text_id}/editions", it.edition, timeout=300)
    it.edition_id = out["id"]
    _save(it, ctx, edition_id=out["id"])
    back = _segments(ctx.client, it.edition_id)
    if _shape(back) != _shape(it.edition["segmentation"]["segments"]):
        raise ApiError(f"edition {out['id']} was stored, but its segments read back differ")
    return f"edition {out['id']}, {len(back)} segments read back"


def _put_alignment(it, ctx):
    src, tgt = it.align_ends()
    ctx.client.put(f"/v2/editions/{src}/alignments/{tgt}", {"alignments": it.pairs}, timeout=300)
    back = set(_pairs(ctx.client, src, tgt))
    want = {(p["source_segment_reference"], p["target_segment_reference"]) for p in it.pairs}
    if back != want:
        raise ApiError(f"alignment read back has {len(back)} pairs, {len(want)} were sent")
    _save(it, ctx, aligned=True)
    return f"{len(back)} pairs read back"


def _patch_details(it, ctx, fields):
    ctx.client.patch(f"/v2/texts/{it.text_id}", fields)
    return "updated: " + ", ".join(fields)


def _patch_content(it, ctx):
    for op in it.ops:
        ctx.client.patch(f"/v2/editions/{it.edition_id}/content", op)
    back = ctx.client.get(f"/v2/editions/{it.edition_id}/content")
    if back != it.edition["content"]:
        raise ApiError("content read back differs from the note after the patch")
    segs = _segments(ctx.client, it.edition_id)
    if _shape(segs)[1] != _shape(it.edition["segmentation"]["segments"])[1]:
        raise ApiError("content is right but the segment spans read back differ")
    _save(it, ctx, content_patched=time.strftime("%Y-%m-%d"))
    return f"{len(it.ops)} edit(s) applied; content and spans read back equal"


def _reseg_delete(it, ctx):
    ctx.client.delete(f"/v2/editions/{it.edition_id}/segmentation")
    left = ctx.client.get_or_none(f"/v2/editions/{it.edition_id}/segmentation")
    if left and left.get("id"):
        raise ApiError(f"segmentation {left['id']} still there after DELETE")
    _save(it, ctx, resegmenting={"step": "segmentation deleted", "date": time.strftime("%Y-%m-%d")})
    return "segmentation deleted"


def _reseg_content(it, ctx):
    live = ctx.client.get(f"/v2/editions/{it.edition_id}/content")
    if live != it.edition["content"]:
        ctx.client.patch(f"/v2/editions/{it.edition_id}/content",
                         {"type": "replace", "start": 0, "end": len(live), "text": it.edition["content"]})
        if ctx.client.get(f"/v2/editions/{it.edition_id}/content") != it.edition["content"]:
            raise ApiError("content read back differs after the replace")
        msg = f"content replaced ({len(live)} -> {len(it.edition['content'])} chars)"
    else:
        msg = "content already equal: nothing to replace"
    _save(it, ctx, resegmenting={"step": "content set", "date": time.strftime("%Y-%m-%d")})
    return msg


def _reseg_post(it, ctx):
    ctx.client.post(f"/v2/editions/{it.edition_id}/segmentation", it.edition["segmentation"], timeout=300)
    back = _segments(ctx.client, it.edition_id)
    if _shape(back) != _shape(it.edition["segmentation"]["segments"]):
        raise ApiError("segmentation stored, but the segments read back differ")
    e = ctx.ledger.entry(it.key)
    e.pop("resegmenting", None)
    e.setdefault("resegmented", []).append(time.strftime("%Y-%m-%d"))
    if it.kind in LINKED_KINDS:
        e["aligned"] = False
    ctx.ledger.save()
    return f"{len(back)} segments read back"


def _delete_alignment(it, ctx):
    src, tgt = it.align_ends()
    ctx.client.delete(f"/v2/editions/{src}/alignments/{tgt}")
    _save(it, ctx, aligned=False)
    return "alignment deleted"


# ------------------------------------------------------------------ plans

def _blocked(it):
    for flag in NO_WRITE:
        if flag in it.flags:
            return f"{flag}: {it.flags[flag]}"
    return None


def plan_push(states, ctx, only):
    plans = []
    for it in states:
        f = it.flags
        p = NotePlan(title=it.title, link=it.link)
        new = "new" in f
        do_text = new and "text" in only
        do_ed = new and "edition" in only
        do_al = it.kind in LINKED_KINDS and "alignment" in only and (
            new or "alignment-missing" in f or "alignment-differs" in f)
        if not (do_text or do_ed or do_al):
            continue
        p.problem = _blocked(it) or (f"root-not-on-env: {f['root-not-on-env']}" if "root-not-on-env" in f else None)
        if not p.problem and new and not (do_text and do_ed):
            p.problem = "a new text needs both `text` and `edition` in --only"
        if not p.problem and do_al and it.pairs is None:
            p.problem = f"alignment-blocked: {f.get('alignment-blocked', 'no alignment pairs')}"
        if not p.problem and do_al and not new and ("refs-renumbered" in f or "resegment-needed" in f):
            p.problem = ("the edition's segment references differ from the note's: the alignment would "
                         "point at segments that are not there (re-segment first: developer)")
        if not p.problem:
            title = next(iter(it.text["title"].values()))
            if do_text:
                link = f", {it.link_field} {it.root_ids[0]}" if it.link_field else ""
                p.steps.append(Step(f"POST /v2/texts  ({it.text['language']}: {title[:40]}{link})",
                                    lambda it=it: _post_text(it, ctx)))
            if do_ed:
                n = len(it.edition["segmentation"]["segments"])
                p.steps.append(Step(f"POST /v2/texts/<text>/editions  ({len(it.edition['content'])} chars, {n} segments)",
                                    lambda it=it: _post_edition(it, ctx)))
            if do_al:
                src, tgt = it.align_ends()
                p.steps.append(Step(f"PUT /v2/editions/{src or '<edition>'}/alignments/{tgt or '<edition>'}  "
                                    f"({len(it.pairs)} pairs, {it.kind} {'<-' if it.kind == 'translation' else '->'} root)",
                                    lambda it=it: _put_alignment(it, ctx)))
        plans.append(p)
    return plans


def plan_update(states, ctx, only):
    plans = []
    for it in states:
        f = it.flags
        if not (("details" in only and "details-changed" in f) or ("content" in only and "content-patchable" in f)):
            continue
        p = NotePlan(title=it.title, link=it.link)
        p.problem = _blocked(it)
        if not p.problem:
            if "details" in only and "details-changed" in f:
                fields = _details_diff(it)
                p.steps.append(Step(f"PATCH /v2/texts/{it.text_id}  ({', '.join(fields)})",
                                    lambda it=it, fields=fields: _patch_details(it, ctx, fields)))
            if "content" in only and "content-patchable" in f:
                lk = [x for x in links(it, ctx) if "recording" in x]
                note = f"; NOTE {lk[0]}: check `library.py audio status` afterwards" if lk else ""
                p.steps.append(Step(f"PATCH /v2/editions/{it.edition_id}/content × {len(it.ops)}  "
                                    f"(in place, boundaries kept){note}", lambda it=it: _patch_content(it, ctx)))
        plans.append(p)
    return plans


def plan_replace(states, ctx):
    """Re-segmentation: the gated, fixed sequence."""
    plans = []
    for it in states:
        f = it.flags
        if not ("resegment-needed" in f or "refs-renumbered" in f):
            continue
        p = NotePlan(title=it.title, link=it.link)
        p.problem = _blocked(it)
        if not p.problem:
            linked = links(it, ctx)
            if linked and not ctx.dev:
                p.problem = ("linked, so only a developer may re-segment it (--dev). Re-segmenting breaks: "
                             + "; ".join(linked)
                             + ". Transclusions in other repos must be redone; translations may or may not "
                               "need redoing (unchanged if the edits stayed inside segments).")
            else:
                why = f.get("resegment-needed") or f.get("refs-renumbered")
                warn = ("  [--dev: breaks " + "; ".join(linked) + "]") if linked else ""
                p.steps.append(Step(f"DELETE /v2/editions/{it.edition_id}/segmentation  ({why}){warn}",
                                    lambda it=it: _reseg_delete(it, ctx)))
                p.steps.append(Step(f"PATCH /v2/editions/{it.edition_id}/content  (one REPLACE of the whole "
                                    "content, only if it differs)", lambda it=it: _reseg_content(it, ctx)))
                p.steps.append(Step(f"POST /v2/editions/{it.edition_id}/segmentation  "
                                    f"({len(it.edition['segmentation']['segments'])} segments)",
                                    lambda it=it: _reseg_post(it, ctx)))
        plans.append(p)
    return plans


def plan_delete(states, ctx, only):
    plans = []
    for it in states:
        p = NotePlan(title=it.title, link=it.link)
        if it.kind not in LINKED_KINDS or only != {"alignment"}:
            p.problem = "only a translation's or commentary's alignment can be deleted (texts are never deleted)"
        elif not (it.edition_id and it.root_ids and it.live_pairs):
            p.problem = f"no alignment on {ctx.env.name}"
        else:
            src, tgt = it.align_ends()
            p.steps.append(Step(f"DELETE /v2/editions/{src}/alignments/{tgt}  ({len(it.live_pairs)} pairs)",
                                lambda it=it: _delete_alignment(it, ctx)))
        plans.append(p)
    return plans


def verify(states, ctx) -> list:
    """Read-only: every state that is not ok, plus ledger/note id agreement."""
    out = []
    for it in states:
        probs = [f"{k}: {v}" for k, v in it.flags.items() if k != "ok"]
        if it.kind in ("text", "commentary") and it.id_source == "ledger" and it.fm.get("text_id") and \
                str(it.fm["text_id"]) != it.text_id:
            probs.append(f"the note's text_id {it.fm['text_id']} is not this environment's {it.text_id}")
        out.append((it, probs))
    return out
