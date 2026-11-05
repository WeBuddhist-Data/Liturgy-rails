"""Build the TARGET content/segmentation for each text from 0-INBOX under the
new policy: heading lines (#, ##, ###) are NOT content; they are TOC only."""
import importlib.util, json, os, re, sys, unicodedata

VAULT = "/Users/tashitsering/Desktop/work/Obsidian/Liturgy-rails"
HERE = os.path.join(VAULT, "4-SYSTEM", "scripts")

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    spec.loader.exec_module(m)
    return m

parser = load("wb_parser", os.path.join(HERE, "parser-root-text", "parser.py"))
REF_RE = parser.REF_RE

def nfc(s): return unicodedata.normalize("NFC", s)
def norm_stem(s): return re.sub(r"[\s།]+$", "", nfc(s))

def read_note(path):
    fm, body = parser._read_source(__import__("pathlib").Path(path))
    return fm, body

# <small>…</small> marks yigchung (ཡིག་ཆུང, small-script rubric). It is an
# annotation, not text: the tags never enter the content, the text between
# them does. This is already how the live backend stores བཀའ་ཐང་བསྡུས་པ།.
YIGCHUNG_RE = re.compile(r"</?small>")

def strip_yigchung(line):
    return YIGCHUNG_RE.sub("", line)

def strip_ids(line):
    """Remove a trailing ^id marker from a line, mimicking the parser."""
    t = line.rstrip()
    m = REF_RE.search(t)
    if m:
        i = t.rfind(m.group(1))
        if i != -1:
            t = t[:i].rstrip()
    return t

def units(body):
    """Return (content_blocks, headings) under the new policy.

    content_blocks: list of dicts {lines: [str,...]}  — heading lines excluded
    headings: list of dicts {level, text, after_block} — index of the content
              block that precedes it (for TOC span placement)
    """
    blocks = parser._extract_blocks(body)
    content_blocks, headings = [], []
    for b in blocks:
        raw = b["lines"]
        if b["is_header"]:
            first = raw[0].lstrip()
            level = len(first) - len(first.lstrip("#"))
            text = strip_ids(first.lstrip("#").strip())
            if text:
                headings.append({"level": level, "text": text,
                                 "after_block": len(content_blocks)})
            continue
        lines = []
        last = -1
        for i in range(len(raw) - 1, -1, -1):
            if raw[i].rstrip():
                last = i
                break
        for i, rl in enumerate(raw):
            t = rl.rstrip()
            if i == last:
                t = strip_ids(t)
            t = strip_yigchung(t).rstrip()
            if not t:
                continue
            lines.append(t)
        if lines:
            content_blocks.append({"lines": lines})
    return content_blocks, headings

def content_of(blocks):
    return "".join(l for b in blocks for l in b["lines"])
