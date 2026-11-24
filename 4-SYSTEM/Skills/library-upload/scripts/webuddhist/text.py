"""Texts, translations and commentaries of a rails vault on one library environment.

  text         root texts       $SOURCE_TEXTS  (file_type root-text / edition)
  translation  translations     $TRANSLATIONS  (file_type translation, root_text: <root note>)
  commentary   commentaries     $COMMENTARIES  (file_type commentary, root_text: <root note>)

Each note is built by the vault's own linter + parser (payloads.py) and compared with
what the environment holds — text, edition content and segmentation, table of contents,
yigchungs, durchens, footnotes and, for a translation or commentary, its alignment to
the root. States (a note can have several):

  ok                   everything matches the note
  new                  not on this environment                              -> push
  details-changed      title / alt titles / license / category / bdrc / date / authors / tags -> update --only details
  content-patchable    content differs, and patching it in place keeps every segment boundary
                       (the server's own span arithmetic, replayed)          -> update --only content
  resegment-needed     boundaries or segment types differ (split/merge/retype) -> replace (gated)
  refs-renumbered      content and spans identical, only references differ  -> replace (gated)
  toc|yigchungs|durchens|footnotes-missing   none on the environment         -> push --only <kind>
  toc|yigchungs|durchens|footnotes-differs   not the note's                  -> update --only <kind>
  toc|yigchungs|durchens|footnotes-extra     on the environment, none in the note -> delete --only <kind>
  alignment-missing / alignment-differs      -> push --only alignment
  alignment-reversed   aligned root -> this text; the library wants this text (source edition)
                       -> root (target edition)  -> push --only alignment (deletes it, sends it the right way)
  alignment-blocked    no alignment can be built on this environment (reason given)
  root-not-on-env      the root text is not on this environment             -> push the root first
  link-differs         translation_of / commentary_of points elsewhere; settable only at creation
  edition-mismatch     the recorded edition is not one of the text's editions
  gone                 an id is recorded but the environment does not have it
  blocked              the vault's linter or parser refused the note (reason quoted)

Annotations (toc, yigchungs, durchens, footnotes) are only compared when the content
matches: their offsets mean nothing against other content.

Re-segmentation (`replace`) is one fixed sequence on the edition: DELETE segmentation ->
PATCH content (one REPLACE of the whole content, only if it changed) -> POST segmentation,
each step read back before the next; then the annotations are sent again. A linked text
(translations, commentaries, alignments, recordings, or itself a translation/commentary)
is refused unless --dev. Texts and editions are never deleted.
"""
from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter as FM
from . import payloads, spans
from .api import ApiError, items
from .plan import NotePlan, Step
from .vault import FOLDERS, FRONTMATTER_ENV, VAULT, rel

LINKED_KINDS = ("translation", "commentary")
ANNOTATIONS = ("toc", "yigchungs", "durchens", "footnotes")
FILE_TYPES = {"text": ("root-text", "edition"), "translation": ("translation",), "commentary": ("commentary",)}
LINK_RE = re.compile(r"\[\[([^\]|#]+)")

FIX = {
    "new": "push", "details-changed": "update --only details", "content-patchable": "update --only content",
    "resegment-needed": "replace (developer only if linked)", "refs-renumbered": "replace (developer only if linked)",
    "types-differ": "harmless as it is; only `replace` changes types (developer only if linked)",
    "alignment-missing": "push --only alignment", "alignment-differs": "push --only alignment",
    "alignment-reversed": "push --only alignment (deletes the reversed one, sends this text -> root)",
    "alignment-blocked": "fix the note's transclusions / block ids, or re-segment the root first",
    "root-not-on-env": "push the root text to this environment first",
    "link-differs": "cannot be patched (set only at creation): ask a developer",
    "edition-mismatch": "check by hand (verify)", "gone": "check by hand (verify)", "blocked": "fix the note",
    "ids-stale": "update --only ids",
}
for _a in ANNOTATIONS:
    FIX[f"{_a}-missing"] = f"push --only {_a}"
    FIX[f"{_a}-differs"] = f"update --only {_a}"
    FIX[f"{_a}-extra"] = f"delete --only {_a} (ask first)"
NO_WRITE = ("blocked", "gone", "edition-mismatch", "link-differs")


# ------------------------------------------------------------------ items

@dataclass
class Item:
    kind: str
    path: Path
    key: str                              # vault-relative path: the ledger key
    fm: dict = field(default_factory=dict)
    p: dict = field(default_factory=dict)  # payloads: text, edition, toc, yigchungs, durchens, footnotes, alignment
    flags: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    ids: dict = field(default_factory=dict)  # text_id, edition_id, toc_id on this env
    id_source: str | None = None             # "ledger" | "frontmatter"
    root_path: Path | None = None
    root_key: str | None = None
    root_ids: tuple | None = None            # (text_id, edition_id) of the root on this env
    root_live_refs: list | None = None
    root_note_refs: list | None = None
    pairs: list | None = None                # [(this ref, root ref)] in this environment's references
    live_text: dict | None = None
    live_content: str | None = None
    live_segs: list | None = None
    live_pairs: list | None = None           # [(this ref, root ref)]
    live_direction: str | None = None        # "root->self" | "self->root"
    live_ann: dict = field(default_factory=dict)
    ops: list | None = None
    stale_fm: dict = field(default_factory=dict)  # frontmatter key -> (id there, id on the server now)

    @property
    def link(self):
        return f"[[{Path(self.key).with_suffix('').as_posix()}|{self.path.stem}]]"

    @property
    def title(self):
        return self.path.stem

    @property
    def link_field(self):
        return {"translation": "translation_of", "commentary": "commentary_of"}.get(self.kind)

    @property
    def text_id(self):
        return self.ids.get("text_id")

    @property
    def edition_id(self):
        return self.ids.get("edition_id")

    def direction(self):
        """Alignment direction used when sending. The library reads an alignment as
        source_edition = the translation or commentary, target_edition = its root, so every
        kind is sent this text -> root. (rails-template sent translations root -> translation;
        an alignment made that way is reported as alignment-reversed.)"""
        return "self->root"

    def align_ends(self):
        """(source edition, target edition) for sending: this text -> its root."""
        root = self.root_ids[1] if self.root_ids else None
        return (self.edition_id, root)

    def live_ends(self):
        """(source edition, target edition) of the alignment the environment holds now."""
        root = self.root_ids[1] if self.root_ids else None
        return (root, self.edition_id) if self.live_direction == "root->self" else (self.edition_id, root)


def collect(kind, paths):
    roots = [Path(p).resolve() for p in paths] if paths else [FOLDERS[kind]]
    out = []
    for r in roots:
        if not r.exists():
            continue
        files = sorted(r.rglob("*.md")) if r.is_dir() else [r]
        out += [f for f in files if not f.name.startswith(("._", "About ", "README"))]
    return out


def _root_note(fm) -> Path | None:
    s = str(fm.get("root_text") or "").strip()
    m = LINK_RE.search(s)
    s = (m.group(1) if m else s).strip()
    if not s:
        return None
    p = VAULT / s
    return p if p.suffix == ".md" else p.with_name(p.name + ".md")


_ROOT_REFS = {}


def _root_refs(root_path: Path):
    k = rel(root_path)
    if k not in _ROOT_REFS:
        try:
            ed = payloads.build(root_path, "text")["edition"]
            _ROOT_REFS[k] = [str(s.get("reference")) for s in ed["segmentation"]["segments"]]
        except Exception:  # noqa: BLE001
            _ROOT_REFS[k] = None
    return _ROOT_REFS[k]


def build(kind, path, keep_edits=False) -> Item | None:
    """-> the built Item, or None for a file that is not a note of this kind."""
    path = Path(path).resolve()
    it = Item(kind=kind, path=path, key=rel(path))
    try:
        it.fm = FM.read(path)
    except Exception:  # noqa: BLE001
        return None
    ftype = str(it.fm.get("file_type") or "").strip()
    if ftype not in FILE_TYPES[kind]:
        return None
    if kind in LINKED_KINDS:
        it.root_path = _root_note(it.fm)
        if not it.root_path or not it.root_path.is_file():
            it.flags["blocked"] = f"root_text {it.fm.get('root_text')!r} does not point at a note"
            return it
        it.root_key = rel(it.root_path)
    try:
        it.p = payloads.build(path, kind, keep_edits=keep_edits)
    except payloads.BuildError as exc:
        it.flags["blocked"] = str(exc)
    return it


# ------------------------------------------------------------------ live reads

@dataclass
class Ctx:
    env: object
    client: object
    ledger: object
    dev: bool = False
    full_replace: bool = False   # developer: re-segment every named note, even when in-place patching would do
    unsupported: set = field(default_factory=set)   # annotation kinds this environment's API lacks


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


def _ids_on_env(ctx, key, path):
    """(ids, source): the env's ledger, else the note's frontmatter ids if this env has that text."""
    e = ctx.ledger.get(key) or {}
    if e.get("text_id"):
        return {k: e[k] for k in ("text_id", "edition_id", "toc_id") if e.get(k)}, "ledger"
    try:
        f = FM.ids(path)
    except Exception:  # noqa: BLE001
        f = {}
    if f.get("text_id"):
        return {k: f[k] for k in ("text_id", "edition_id", "toc_id") if f.get(k)}, "frontmatter"
    return {}, None


def _fetch(it: Item, ctx: Ctx):
    c = ctx.client
    it.ids, it.id_source = _ids_on_env(ctx, it.key, it.path)
    if it.text_id:
        it.live_text = c.get_or_none(f"/v2/texts/{it.text_id}")
    if it.kind in LINKED_KINDS and it.root_path:
        rids, _ = _ids_on_env(ctx, it.root_key, it.root_path)
        if rids.get("text_id") and c.get_or_none(f"/v2/texts/{rids['text_id']}") is not None:
            it.root_ids = (rids["text_id"], rids.get("edition_id"))
            if it.root_ids[1]:
                it.root_live_refs = [str(s.get("reference")) for s in _segments(c, it.root_ids[1])]
    if it.live_text and it.edition_id in (it.live_text.get("editions") or []):
        eid = it.edition_id
        it.live_content = c.get(f"/v2/editions/{eid}/content")
        it.live_segs = _segments(c, eid)
        it.live_ann = {}
        for a, path in (("toc", "table-of-contents"), ("yigchungs", "yigchungs"), ("durchens", "durchens"),
                        ("footnotes", "footnotes")):
            if a in ctx.unsupported:
                it.live_ann[a] = None
                continue
            try:
                it.live_ann[a] = items(c.get(f"/v2/editions/{eid}/{path}"))
            except ApiError as exc:
                if exc.status not in (404, 405):
                    raise
                ctx.unsupported.add(a)          # this environment's API has no such endpoint (yet)
                it.live_ann[a] = None
        if it.root_ids and it.root_ids[1]:
            fwd = _pairs(c, it.root_ids[1], eid)
            if fwd:
                it.live_pairs, it.live_direction = [(b, a) for a, b in fwd], "root->self"
            else:
                back = _pairs(c, eid, it.root_ids[1])
                if back:
                    it.live_pairs, it.live_direction = back, "self->root"


def assess_all(kind, paths, ctx: Ctx, progress=True, keep_edits=False) -> list:
    files = collect(kind, paths)
    tty = progress and sys.stderr.isatty()
    built, done = [], 0

    def make(p):
        return build(kind, p, keep_edits=keep_edits)

    with ThreadPoolExecutor(4) as ex:
        for it in ex.map(make, files):
            done += 1
            if tty:
                print(f"\r  linting + parsing {done}/{len(files)} …", end="", file=sys.stderr, flush=True)
            if it is not None:
                built.append(it)

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
            if tty:
                print(f"\r  reading {done}/{len(built)} on {ctx.env.name} …      ", end="", file=sys.stderr, flush=True)
    if tty:
        print("\r" + " " * 70 + "\r", end="", file=sys.stderr)
    for it in built:
        _classify(it, ctx)
    return built


# ------------------------------------------------------------------ comparing

def _shape(segs):
    return ([str(s.get("reference")) for s in segs], [[(l["start"], l["end"]) for l in s["lines"]] for s in segs],
            [s.get("type") for s in segs])


def _refs_detail(live, built):
    d = [(a, b) for a, b in zip(live, built) if a != b]
    return (f"{len(d)} reference(s) differ, e.g. " + ", ".join(f"{a}→{b}" for a, b in d[:3])) if d \
        else "segment count differs"


def _segmentwise_ops(old, new, l_spans, b_spans):
    """Edits found segment by segment (linear, precise on texts of any size), when both sides have
    the same number of segments and the segments cover the whole content. -> ops, or None."""
    if len(l_spans) != len(b_spans) or not l_spans:
        return None
    lo = [(s[0][0], s[-1][1]) for s in l_spans]
    bo = [(s[0][0], s[-1][1]) for s in b_spans]
    if lo[0][0] != 0 or bo[0][0] != 0 or lo[-1][1] != len(old) or bo[-1][1] != len(new):
        return None
    if any(a[1] != b[0] for a, b in zip(lo, lo[1:])) or any(a[1] != b[0] for a, b in zip(bo, bo[1:])):
        return None
    ops = []
    for (ls, le), (bs, be) in zip(lo, bo):
        x, y = old[ls:le], new[bs:be]
        if x == y:
            continue
        for op in spans.make_ops(x, y):          # positions relative to the live segment
            op = dict(op)
            if "position" in op:
                op["position"] += ls
            else:
                op["start"] += ls
                op["end"] += ls
            ops.append(op)
    ops.sort(key=lambda o: o.get("position", o.get("start")), reverse=True)
    return ops


def _compare_edition(it: Item):
    new = it.p["edition"]
    b_refs, b_spans, b_types = _shape(new["segmentation"]["segments"])
    l_refs, l_spans, l_types = _shape(it.live_segs or [])
    renum = b_refs != l_refs
    if b_spans_equal_types_differ(b_spans, l_spans, b_types, l_types):
        n = sum(1 for x, y in zip(b_types, l_types) if x != y)
        examples = [f"{y}→{x}" for x, y in zip(b_types, l_types) if x != y][:3]
        it.flags["types-differ"] = f"{n} segment type(s) differ, e.g. " + ", ".join(examples)
    if new["content"] == it.live_content:
        if b_spans == l_spans:
            return ("refs-renumbered", _refs_detail(l_refs, b_refs)) if renum else ("ok", "")
        why = (f"{len(l_spans)} -> {len(b_spans)} segments" if len(b_spans) != len(l_spans)
               else "segment boundaries differ")
        return "resegment-needed", f"same text, {why}"
    ops = _segmentwise_ops(it.live_content, new["content"], l_spans, b_spans)
    if ops is None:
        ops = spans.make_ops(it.live_content, new["content"])
    text, flat = it.live_content, [sp for s in l_spans for sp in s]
    owner = [i for i, s in enumerate(l_spans) for _ in s]
    for op in ops:
        flat = spans.apply_op_to_spans(flat, op)
        text = spans.apply_op_to_text(text, op)
    grouped = {}
    for sp, i in zip(flat, owner):
        if sp is not None:
            grouped.setdefault(i, []).append(sp)
    replayed = [grouped[i] for i in sorted(grouped)]
    if text == new["content"] and replayed == b_spans:
        it.ops = ops
        extra = f"; segment references also differ ({_refs_detail(l_refs, b_refs)})" if renum else ""
        return "content-patchable", f"{len(ops)} edit(s) in place, every segment boundary kept{extra}"
    why = ("content edits move segment boundaries" if text == new["content"]
           else "content cannot be patched in place")
    return "resegment-needed", f"{why} ({len(l_spans)} -> {len(b_spans)} segments)"


def b_spans_equal_types_differ(b_spans, l_spans, b_types, l_types):
    """Same number of segments, different type labels (verse / paragraph / …). Types can only be
    changed by re-segmenting; the parser that built the live edition may simply type differently."""
    return len(b_types) == len(l_types) and b_types != l_types


def _norm_contrib(cs):
    return sorted((c.get("type"), c.get("id") or c.get("bdrc_id") or c.get("bdrc"), c.get("role")) for c in cs or [])


def _details_diff(it: Item) -> dict:
    live, want = it.live_text or {}, it.p.get("text") or {}
    out = {}
    for k, v in want.items():
        if k in ("translation_of", "commentary_of") or v in (None, "", [], {}):
            continue
        lv = live.get(k)
        if k == "contributions":
            if _norm_contrib(v) != _norm_contrib(lv):
                out[k] = v
        elif k == "tag_ids":
            # Additive only: tags are also attached outside the notes (a tagging pass, the
            # cascade from a root to its translations). Never send a list that would drop one.
            missing = [t for t in v if t not in (lv or [])]
            if missing:
                out[k] = list(lv or []) + missing
        elif v != lv:
            out[k] = v
    return out


def _strip_ids(x):
    if isinstance(x, dict):
        return {k: _strip_ids(v) for k, v in sorted(x.items()) if k not in ("id", "edition_id", "text_id")
                and v not in (None, [], {})}
    if isinstance(x, list):
        return [_strip_ids(v) for v in x]
    return x


def ann_sets(kind, data):
    """Comparable form of one annotation kind (payload list or live list)."""
    if kind == "toc":
        return _strip_ids(data or [])
    if kind == "yigchungs":
        return sorted((a["span"]["start"], a["span"]["end"]) for a in data or [])
    if kind == "durchens":
        return sorted((a["span"]["start"], a["span"]["end"], a.get("text", "")) for a in data or [])
    return sorted((a["position"], a.get("text", "")) for a in data or [])


def _live_toc_sections(it):
    tocs = it.live_ann.get("toc") or []
    return tocs[0].get("sections", []) if len(tocs) == 1 else [t.get("sections", []) for t in tocs]


def _compare_annotations(it: Item):
    f = it.flags
    for a in ANNOTATIONS:
        if it.live_ann.get(a) is None:
            if it.p.get(a):
                it.notes.append(f"{len(it.p[a])} {a} in the note, but this environment has no {a} endpoint")
            continue
        want = ann_sets(a, it.p.get(a))
        have = ann_sets(a, _live_toc_sections(it) if a == "toc" else it.live_ann.get(a))
        if a == "toc" and len(it.live_ann.get("toc") or []) > 1:
            f["toc-differs"] = f"{len(it.live_ann['toc'])} tables of contents on the edition"
            continue
        if want == have:
            continue
        if want and not have:
            f[f"{a}-missing"] = f"{len(it.p.get(a) or [])} in the note, none on the environment"
        elif have and not want:
            f[f"{a}-extra"] = f"{len(have)} on the environment, none in the note"
        else:
            f[f"{a}-differs"] = "not the note's"


def _to_live_root(it: Item):
    """root note ref -> the root's reference on this env (the vault may have renumbered after upload)."""
    note, live = it.root_note_refs, it.root_live_refs
    if note is None or live is None:
        return None
    if note == live:
        return {r: r for r in note}
    if len(note) == len(live):
        return dict(zip(note, live))
    ls = set(live)
    return {r: r for r in note if r in ls}


def _make_pairs(it: Item, ctx: Ctx):
    f = it.flags
    if it.root_live_refs is None:
        f["alignment-blocked"] = f"the root's edition cannot be read on {ctx.env.name}"
        return
    it.root_note_refs = _root_refs(it.root_path)
    own = [str(s.get("reference")) for s in it.p["edition"]["segmentation"]["segments"]]
    note_pairs = [(str(x["source_segment_reference"]), str(x["target_segment_reference"]))
                  for x in (it.p.get("alignment") or [])]
    if not note_pairs and it.kind == "translation" and it.root_note_refs and own == it.root_note_refs:
        note_pairs = [(r, r) for r in own]          # no transclusions: block N renders root block N
        it.notes.append("aligned by matching block ids (no transclusions)")
    if not note_pairs:
        f["alignment-blocked"] = "no transclusions of the root, and the block ids do not match the root's"
        return
    m = _to_live_root(it)
    if m is None:
        f["alignment-blocked"] = "the root note cannot be built"
        return
    missing = sorted({t for _, t in note_pairs if t not in m})
    if missing:
        f["alignment-blocked"] = (f"root block(s) {', '.join('^' + x for x in missing[:6])} are not segments of the "
                                  f"root on {ctx.env.name} (re-segment the root first)")
        return
    it.pairs = list(dict.fromkeys((s, m[t]) for s, t in note_pairs))


def _classify(it: Item, ctx: Ctx):
    f = it.flags
    if "blocked" in f or "gone" in f:
        return
    if it.kind in LINKED_KINDS:
        if not it.root_ids:
            f["root-not-on-env"] = f"root {it.root_key} is not on {ctx.env.name}"
            return
        _make_pairs(it, ctx)
    if not it.text_id:
        f["new"] = f"not on {ctx.env.name}"
        return
    if it.live_text is None:
        if it.id_source == "frontmatter" and ctx.env.name != FRONTMATTER_ENV:
            f["new"] = f"not on {ctx.env.name} (the frontmatter ids are {FRONTMATTER_ENV}'s)"
            it.ids = {}
        else:
            f["gone"] = f"text {it.text_id} is recorded ({it.id_source}) but not on {ctx.env.name}"
        return
    if it.link_field and it.live_text.get(it.link_field) != it.root_ids[0]:
        f["link-differs"] = f"{it.link_field} is {it.live_text.get(it.link_field)}, the root is {it.root_ids[0]}"
    if it.edition_id not in (it.live_text.get("editions") or []):
        f["edition-mismatch"] = f"edition {it.edition_id} is not among {it.live_text.get('editions')}"
        return
    diff = _details_diff(it)
    if diff:
        f["details-changed"] = "differs: " + ", ".join(diff)
    state, detail = _compare_edition(it)
    if state != "ok":
        f[state] = detail
    if state in ("ok", "refs-renumbered") and it.live_content == it.p["edition"]["content"]:
        _compare_annotations(it)
    else:
        it.notes.append("annotations not compared: the content differs")
    if it.kind in LINKED_KINDS and it.pairs is not None:
        want, have = set(it.pairs), set(it.live_pairs or [])
        if not have:
            f["alignment-missing"] = f"no alignment with the root on {ctx.env.name}"
        elif it.live_direction == "root->self":
            f["alignment-reversed"] = (f"{len(have)} pairs on {ctx.env.name} with the root as source edition; "
                                       f"the library expects this text as source, the root as target")
        elif want != have:
            f["alignment-differs"] = f"{len(have)} pairs on {ctx.env.name}, {len(want)} from the note"
    it.stale_fm = _stale_frontmatter(it, ctx)
    if it.stale_fm:
        f["ids-stale"] = "frontmatter " + ", ".join(f"{k} {a} is gone, the server has {b}"
                                                    for k, (a, b) in it.stale_fm.items())
    if not f:
        f["ok"] = ""


def _stale_frontmatter(it: Item, ctx: Ctx) -> dict:
    """On the frontmatter environment: frontmatter ids that no longer exist on the server, each
    with the one id that replaced it there. Only a clear successor is offered."""
    if ctx.env.name != FRONTMATTER_ENV or not it.live_text:
        return {}
    try:
        fm = FM.ids(it.path)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if fm.get("text_id") and it.text_id and fm["text_id"] != it.text_id and \
            ctx.client.get_or_none(f"/v2/texts/{fm['text_id']}") is None:
        out["text_id"] = (fm["text_id"], it.text_id)
    editions = it.live_text.get("editions") or []
    if fm.get("edition_id") and it.edition_id and fm["edition_id"] != it.edition_id and \
            fm["edition_id"] not in editions and it.edition_id in editions:
        out["edition_id"] = (fm["edition_id"], it.edition_id)
    tocs = [str(t.get("id")) for t in (it.live_ann.get("toc") or []) if t.get("id")]
    if fm.get("toc_id") and len(tocs) == 1 and fm["toc_id"] not in tocs:
        out["toc_id"] = (fm["toc_id"], tocs[0])
    return out


# ------------------------------------------------------------------ links (the re-segmentation gate)

def _vault_dependents(root_key) -> list:
    out = []
    for kind in LINKED_KINDS:
        folder = FOLDERS[kind]
        if not folder.is_dir():
            continue
        for p in folder.rglob("*.md"):
            head = p.read_text(encoding="utf-8", errors="replace")[:3000]
            m = re.search(r"^root_text:\s*(.+)$", head, re.M)
            if m:
                rp = _root_note({"root_text": m.group(1).strip().strip("'\"")})
                if rp and rel(rp) == root_key:
                    out.append(f"{kind} {p.stem}")
    return out


def links(it: Item, ctx: Ctx) -> list:
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
            out.append("notes in the vault built on it: " + ", ".join(dep[:10]))
    return out


# ------------------------------------------------------------------ steps

def _record(it: Item, ctx: Ctx, supersedes=None, **ids):
    """Ledger always; frontmatter only on the frontmatter environment, never overwriting —
    except an id in `supersedes`, which the caller confirmed is gone from the server."""
    e = ctx.ledger.entry(it.key)
    e.update({k: v for k, v in ids.items() if v is not None})
    ctx.ledger.save()
    fm_ids = {k: v for k, v in ids.items() if k in FM.ID_KEYS and v}
    if ctx.env.name == FRONTMATTER_ENV and fm_ids:
        try:
            written = FM.write_ids(it.path, supersedes=supersedes, **fm_ids)
        except ValueError as exc:
            return f" (frontmatter NOT updated: {exc})"
        if written:
            return f" (frontmatter: {', '.join(written)} written)"
    return ""


def _fix_frontmatter_ids(it, ctx):
    """update --only ids: put the server's current id where the frontmatter holds one that is gone."""
    new = {k: b for k, (a, b) in it.stale_fm.items()}
    gone = {k: {a} for k, (a, b) in it.stale_fm.items()}
    it.ids.update({k: v for k, v in new.items() if k in ("text_id", "edition_id", "toc_id")})
    msg = _record(it, ctx, supersedes=gone, **new)
    if any(FM.ids(it.path).get(k) != v for k, v in new.items()):
        raise ApiError("frontmatter read back still differs" + msg)
    return "; ".join(f"{k} {a} -> {b}" for k, (a, b) in it.stale_fm.items()) + msg


def _post_text(it, ctx):
    body = {k: v for k, v in (it.p.get("text") or {}).items() if v not in (None, "", [], {})}
    body.pop("translation_of", None)
    body.pop("commentary_of", None)
    if it.link_field:
        body[it.link_field] = it.root_ids[0]
    out = ctx.client.post("/v2/texts", body)
    it.ids["text_id"] = out["id"]
    return f"text {out['id']}" + _record(it, ctx, text_id=out["id"], note=it.key)


def _post_edition(it, ctx):
    ed = it.p["edition"]
    out = ctx.client.post(f"/v2/texts/{it.text_id}/editions", ed, timeout=300)
    it.ids["edition_id"] = out["id"]
    msg = _record(it, ctx, edition_id=out["id"])
    back = _segments(ctx.client, out["id"])
    if _shape(back) != _shape(ed["segmentation"]["segments"]):
        raise ApiError(f"edition {out['id']} was stored, but its segments read back differ")
    return f"edition {out['id']}, {len(back)} segments read back" + msg


def _post_toc(it, ctx, replaced=()):
    """Post the note's TOC; `replaced` are the TOC ids just deleted, which the frontmatter may drop."""
    out = ctx.client.post(f"/v2/editions/{it.edition_id}/table-of-contents", {"sections": it.p["toc"]})
    it.ids["toc_id"] = out.get("id")
    return f"toc {out.get('id')}" + _record(it, ctx, supersedes={"toc_id": set(replaced)}, toc_id=out.get("id"))


def _post_annotations(it, ctx, a):
    data = it.p.get(a) or []
    ctx.client.post(f"/v2/editions/{it.edition_id}/{a}", data, timeout=300)
    back = items(ctx.client.get(f"/v2/editions/{it.edition_id}/{a}"))
    if ann_sets(a, back) != ann_sets(a, data):
        raise ApiError(f"{a} read back differ from what was sent")
    _record(it, ctx, **{f"{a}_sent": time.strftime("%Y-%m-%d")})
    return f"{len(back)} {a} read back"


def _sync_annotations(it, ctx, a):
    """Make the edition's annotations of kind `a` exactly the note's (delete what differs, post what is missing)."""
    eid, c = it.edition_id, ctx.client
    if a == "toc":
        deleted = []
        for t in items(c.get(f"/v2/editions/{eid}/table-of-contents")):
            c.delete(f"/v2/table-of-contents/{t['id']}")
            deleted.append(str(t["id"]))
        if it.p.get("toc"):
            return _post_toc(it, ctx, replaced=deleted) + " (old one(s) deleted)"
        return "table of contents deleted"
    if a == "footnotes":
        c.delete(f"/v2/editions/{eid}/footnotes")
        return _post_annotations(it, ctx, a) if it.p.get(a) else "footnotes deleted"
    live = items(c.get(f"/v2/editions/{eid}/{a}"))
    want = it.p.get(a) or []
    key = (lambda x: ann_sets(a, [x])[0])
    want_keys = {key(x) for x in want}
    live_keys = {key(x): x for x in live}
    single = a.rstrip("s")
    for k, x in live_keys.items():
        if k not in want_keys:
            c.delete(f"/v2/{a}/{x['id']}")
    missing = [x for x in want if key(x) not in live_keys]
    if missing:
        c.post(f"/v2/editions/{eid}/{a}", missing, timeout=300)
    back = items(c.get(f"/v2/editions/{eid}/{a}"))
    if ann_sets(a, back) != ann_sets(a, want):
        raise ApiError(f"{a} read back differ from the note")
    return f"{single}s now match: {len(back)}"


def _put_alignment(it, ctx):
    src, tgt = it.align_ends()                  # this text -> root
    body = [{"source_segment_reference": s, "target_segment_reference": t} for s, t in it.pairs]
    ctx.client.put(f"/v2/editions/{src}/alignments/{tgt}", {"alignments": body}, timeout=300)
    back = set(_pairs(ctx.client, src, tgt))
    if back != set(it.pairs):
        raise ApiError(f"alignment read back has {len(back)} pairs, {len(it.pairs)} were sent")
    return f"{len(back)} pairs read back" + _record(it, ctx, aligned_to_edition_id=it.root_ids[1])


def _patch_details(it, ctx, fields):
    ctx.client.patch(f"/v2/texts/{it.text_id}", fields)
    return "updated: " + ", ".join(fields)


def _patch_content(it, ctx):
    for op in it.ops:
        ctx.client.patch(f"/v2/editions/{it.edition_id}/content", op)
    if ctx.client.get(f"/v2/editions/{it.edition_id}/content") != it.p["edition"]["content"]:
        raise ApiError("content read back differs from the note after the patch")
    if _shape(_segments(ctx.client, it.edition_id))[1] != _shape(it.p["edition"]["segmentation"]["segments"])[1]:
        raise ApiError("content is right but the segment spans read back differ")
    _record(it, ctx, content_patched=time.strftime("%Y-%m-%d"))
    return f"{len(it.ops)} edit(s) applied; content and spans read back equal"


def _reseg_delete(it, ctx):
    ctx.client.delete(f"/v2/editions/{it.edition_id}/segmentation")
    left = ctx.client.get_or_none(f"/v2/editions/{it.edition_id}/segmentation")
    if left and left.get("id"):
        raise ApiError(f"segmentation {left['id']} still there after DELETE")
    _record(it, ctx, resegmenting={"step": "segmentation deleted", "date": time.strftime("%Y-%m-%d")})
    return "segmentation deleted"


def _reseg_content(it, ctx):
    live = ctx.client.get(f"/v2/editions/{it.edition_id}/content")
    new = it.p["edition"]["content"]
    if live != new:
        ctx.client.patch(f"/v2/editions/{it.edition_id}/content",
                         {"type": "replace", "start": 0, "end": len(live), "text": new})
        if ctx.client.get(f"/v2/editions/{it.edition_id}/content") != new:
            raise ApiError("content read back differs after the replace")
        msg = f"content replaced ({len(live)} -> {len(new)} chars)"
    else:
        msg = "content already equal: nothing to replace"
    _record(it, ctx, resegmenting={"step": "content set", "date": time.strftime("%Y-%m-%d")})
    return msg


def _reseg_post(it, ctx):
    seg = it.p["edition"]["segmentation"]
    ctx.client.post(f"/v2/editions/{it.edition_id}/segmentation", seg, timeout=300)
    if _shape(_segments(ctx.client, it.edition_id)) != _shape(seg["segments"]):
        raise ApiError("segmentation stored, but the segments read back differ")
    e = ctx.ledger.entry(it.key)
    e.pop("resegmenting", None)
    e.setdefault("resegmented", []).append(time.strftime("%Y-%m-%d"))
    ctx.ledger.save()
    return f"{len(seg['segments'])} segments read back"


def _delete_ann(it, ctx, a):
    eid, c = it.edition_id, ctx.client
    if a == "toc":
        for t in items(c.get(f"/v2/editions/{eid}/table-of-contents")):
            c.delete(f"/v2/table-of-contents/{t['id']}")
    elif a == "footnotes":
        c.delete(f"/v2/editions/{eid}/footnotes")
    else:
        for x in items(c.get(f"/v2/editions/{eid}/{a}")):
            c.delete(f"/v2/{a}/{x['id']}")
    e = ctx.ledger.entry(it.key)
    e.setdefault("deleted", []).append({"what": a, "date": time.strftime("%Y-%m-%d")})
    ctx.ledger.save()
    return f"{a} deleted"


def _delete_alignment(it, ctx):
    src, tgt = it.live_ends()                   # whichever direction the environment holds
    ctx.client.delete(f"/v2/editions/{src}/alignments/{tgt}")
    e = ctx.ledger.entry(it.key)
    e.setdefault("deleted", []).append({"what": "alignment", "date": time.strftime("%Y-%m-%d")})
    ctx.ledger.save()
    return "alignment deleted"


# ------------------------------------------------------------------ plans

def _blocked(it):
    for flag in NO_WRITE:
        if flag in it.flags:
            return f"{flag}: {it.flags[flag]}"
    return None


def _creation_problem(it, ctx):
    """Rules carried over from root-text-, translation- and commentary-upload, checked before a text is created."""
    text = it.p.get("text") or {}
    lang = text.get("language")
    foreign = sorted({k for alt in text.get("alt_titles") or [] for k in alt if k != lang})
    if foreign:
        return (f"alt_titles in another language ({', '.join(foreign)}): the library merges them into the title "
                "map, where titles are unique per language, and they cannot be removed afterwards — remove them "
                "from the note")
    if it.kind == "commentary" and it.fm.get("translation_of"):
        return "a commentary must not carry translation_of"
    if it.kind == "commentary" and it.root_ids:
        root = ctx.client.get_or_none(f"/v2/texts/{it.root_ids[0]}") or {}
        mine = set((text.get("title") or {}).values())
        for cid in root.get("commentaries") or []:
            other = ctx.client.get_or_none(f"/v2/texts/{cid}") or {}
            if mine & set((other.get("title") or {}).values()):
                return f"the root already has a commentary with this title ({cid}): not created twice"
    return None


def plan_push(states, ctx, only):
    plans = []
    for it in states:
        f = it.flags
        p = NotePlan(title=it.title, link=it.link)
        if "root-not-on-env" in f:
            p.problem = f"root-not-on-env: {f['root-not-on-env']} — push the root text first"
            plans.append(p)
            continue
        new = "new" in f
        want = {
            "text": new and "text" in only,
            "edition": new and "edition" in only,
            "alignment": it.kind in LINKED_KINDS and "alignment" in only
                         and (new or "alignment-missing" in f or "alignment-differs" in f
                              or "alignment-reversed" in f),
        }
        for a in ANNOTATIONS:
            want[a] = (a in only and bool(it.p.get(a)) and a not in ctx.unsupported
                       and (new or f"{a}-missing" in f))
        if not any(want.values()):
            continue
        p.problem = _blocked(it) or (f"root-not-on-env: {f['root-not-on-env']}" if "root-not-on-env" in f else None)
        if not p.problem and new and not (want["text"] and want["edition"]):
            p.problem = "a new text needs both `text` and `edition` in --only"
        if not p.problem and new:
            p.problem = _creation_problem(it, ctx)
        if not p.problem and want["alignment"] and it.pairs is None:
            p.problem = f"alignment-blocked: {f.get('alignment-blocked', 'no alignment pairs')}"
        if not p.problem and want["alignment"] and not new and ("refs-renumbered" in f or "resegment-needed" in f):
            p.problem = "the edition's references differ from the note's: re-segment first (developer)"
        if not p.problem:
            title = next(iter((it.p.get("text") or {}).get("title", {"?": it.title}).values()))
            if want["text"]:
                link = f", {it.link_field} {it.root_ids[0]}" if it.link_field else ""
                p.steps.append(Step(f"POST /v2/texts  ({title[:40]}{link})", lambda it=it: _post_text(it, ctx)))
            if want["edition"]:
                ed = it.p["edition"]
                p.steps.append(Step(f"POST /v2/texts/<text>/editions  ({len(ed['content'])} chars, "
                                    f"{len(ed['segmentation']['segments'])} segments)",
                                    lambda it=it: _post_edition(it, ctx)))
            if want["toc"]:
                p.steps.append(Step(f"POST /v2/editions/<edition>/table-of-contents  ({len(it.p['toc'])} top section(s))",
                                    lambda it=it: _post_toc(it, ctx)))
            for a in ("yigchungs", "durchens", "footnotes"):
                if want[a]:
                    p.steps.append(Step(f"POST /v2/editions/<edition>/{a}  ({len(it.p[a])})",
                                        lambda it=it, a=a: _post_annotations(it, ctx, a)))
            if want["alignment"]:
                if "alignment-reversed" in f:
                    rs, rt = it.live_ends()
                    p.steps.append(Step(f"DELETE /v2/editions/{rs}/alignments/{rt}  ({len(it.live_pairs)} pairs, "
                                        f"reversed: root as source)",
                                        lambda it=it: _delete_alignment(it, ctx)))
                src, tgt = it.align_ends()
                p.steps.append(Step(f"PUT /v2/editions/{src or '<edition>'}/alignments/{tgt}  "
                                    f"({len(it.pairs)} pairs, this text -> root)",
                                    lambda it=it: _put_alignment(it, ctx)))
        plans.append(p)
    return plans


def plan_update(states, ctx, only):
    plans = []
    for it in states:
        f = it.flags
        todo = [k for k in ("details", "content", *ANNOTATIONS, "ids") if k in only and (
            (k == "details" and "details-changed" in f) or (k == "content" and "content-patchable" in f)
            or (k == "ids" and "ids-stale" in f) or f"{k}-differs" in f)]
        if not todo:
            continue
        p = NotePlan(title=it.title, link=it.link)
        p.problem = _blocked(it)
        if not p.problem:
            if "details" in todo:
                fields = _details_diff(it)
                p.steps.append(Step(f"PATCH /v2/texts/{it.text_id}  ({', '.join(fields)})",
                                    lambda it=it, fields=fields: _patch_details(it, ctx, fields)))
            if "content" in todo:
                rec = [x for x in links(it, ctx) if "recording" in x]
                note = f"; NOTE {rec[0]}: check `audio status` afterwards" if rec else ""
                p.steps.append(Step(f"PATCH /v2/editions/{it.edition_id}/content × {len(it.ops)}  (in place){note}",
                                    lambda it=it: _patch_content(it, ctx)))
            for a in ANNOTATIONS:
                if a in todo:
                    p.steps.append(Step(f"sync {a} on {it.edition_id}  (delete what differs, post what is missing)",
                                        lambda it=it, a=a: _sync_annotations(it, ctx, a)))
            if "ids" in todo:
                what = ", ".join(f"{k} {a} -> {b}" for k, (a, b) in it.stale_fm.items())
                p.steps.append(Step(f"write frontmatter ids  ({what}; the old ones are gone from {ctx.env.name})",
                                    lambda it=it: _fix_frontmatter_ids(it, ctx)))
        plans.append(p)
    return plans


def plan_replace(states, ctx):
    plans = []
    for it in states:
        f = it.flags
        resegment = "resegment-needed" in f or "refs-renumbered" in f or "types-differ" in f
        # --full-replace (developer only): every named note on the
        # environment gets the whole sequence, even when its boundaries are unchanged.
        if not resegment and not (ctx.full_replace and "new" not in f and "gone" not in f):
            continue
        p = NotePlan(title=it.title, link=it.link)
        p.problem = _blocked(it)
        if not p.problem and "content-patchable" in f and not ctx.full_replace:
            p.problem = "patch the content in place first (update --only content), then re-segment if still wanted"
        if not p.problem:
            linked = links(it, ctx)
            if linked and not ctx.dev:
                p.problem = ("linked, so only a developer may re-segment it (--dev). Re-segmenting breaks: "
                             + "; ".join(linked) + ". Transclusions in other repos must be redone; translations "
                             "may or may not need redoing (unchanged if the edits stayed inside segments).")
            else:
                why = (f.get("resegment-needed") or f.get("refs-renumbered") or f.get("types-differ")
                       or "full replace requested by a developer (--full-replace)")
                warn = ("  [--dev: breaks " + "; ".join(linked) + "]") if linked else ""
                eid = it.edition_id
                p.steps.append(Step(f"DELETE /v2/editions/{eid}/segmentation  ({why}){warn}",
                                    lambda it=it: _reseg_delete(it, ctx)))
                p.steps.append(Step(f"PATCH /v2/editions/{eid}/content  (one REPLACE of the whole content, if it differs)",
                                    lambda it=it: _reseg_content(it, ctx)))
                p.steps.append(Step(f"POST /v2/editions/{eid}/segmentation  "
                                    f"({len(it.p['edition']['segmentation']['segments'])} segments)",
                                    lambda it=it: _reseg_post(it, ctx)))
                for a in ANNOTATIONS:
                    if a not in ctx.unsupported and (it.p.get(a) or it.live_ann.get(a)):
                        p.steps.append(Step(f"sync {a} on {eid}  (offsets are against the new content)",
                                            lambda it=it, a=a: _sync_annotations(it, ctx, a)))
        plans.append(p)
    return plans


def plan_delete(states, ctx, only):
    plans = []
    allowed = set(ANNOTATIONS) | {"alignment"}
    for it in states:
        p = NotePlan(title=it.title, link=it.link)
        if only - allowed:
            p.problem = "only alignment, toc, yigchungs, durchens or footnotes can be deleted (texts never)"
        elif not it.edition_id or it.live_text is None:
            p.problem = f"nothing from this note on {ctx.env.name}"
        else:
            for a in ANNOTATIONS:
                live = (it.live_ann or {}).get(a)
                if a in only and live:
                    p.steps.append(Step(f"DELETE {a} of {it.edition_id}  ({len(live)})",
                                        lambda it=it, a=a: _delete_ann(it, ctx, a)))
            if "alignment" in only:
                if it.kind in LINKED_KINDS and it.live_pairs:
                    src, tgt = it.live_ends()
                    p.steps.append(Step(f"DELETE /v2/editions/{src}/alignments/{tgt}  ({len(it.live_pairs)} pairs)",
                                        lambda it=it: _delete_alignment(it, ctx)))
            if not p.steps:
                p.problem = f"none of {', '.join(sorted(only))} on {ctx.env.name}"
        plans.append(p)
    return plans


def verify(states, ctx) -> list:
    out = []
    for it in states:
        probs = [f"{k}: {v}" for k, v in it.flags.items() if k != "ok"]
        if ctx.env.name == FRONTMATTER_ENV and it.id_source == "ledger":
            try:
                fm = FM.ids(it.path)
            except Exception:  # noqa: BLE001
                fm = {}
            for k in ("text_id", "edition_id"):
                if fm.get(k) and fm[k] != it.ids.get(k):
                    probs.append(f"frontmatter {k} {fm[k]} is not the ledger's {it.ids.get(k)}")
        out.append((it, probs))
    return out
