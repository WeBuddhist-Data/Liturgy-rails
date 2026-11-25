"""Durchen (མཆན་འགྲེལ་ / variant-reading notes) written as Obsidian footnotes.

Shared by linter-root-text, linter-commentary, parser-root-text and
parser-commentary: the same file sits in each folder, byte-identical, so every
tool stays self-contained. Change one copy, copy it to the other three.

In the source a durchen is a footnote marker placed right after the text it
annotates, and a definition at the end of the file:

    རྣམ་དག་ཆོས་ཀྱི་དབྱིངས་ལ༌[^d1]རོལ་མཛད་ཅིང་། །
    [^d1]: ལ་ ༼སྣར། པེ།༽ ལས་

The label says what a footnote is (see kind()): [^dN] is a durchen, [^fN] a
footnote (an editorial remark, a source citation, a colophon printed as a
footnote). A bare number [^N] is the old form and is still read as a durchen
until the sources are relabelled. At the end of the file the durchen
definitions come first, d1…dK in marker order, then the footnotes, f1…fM.

A footnote has a position, not a span: the gap between two characters, from 0
(before the first character) to len(content) (after the last). The marker goes
right before content[position]. For "ཀཁག": 0 → ●ཀཁག, 2 → ཀཁ●ག, 3 → ཀཁག●.
Positions count Unicode code points, and adding a footnote never changes the
content. The position is simply where the marker stood once the markers are
taken out (see footnote_payload()).

The note reads "<lemma> ༼<witness sigla>༽ <variant>": the lemma is the base
reading the note is about. The durchen span is that lemma, found by matching it
backwards from the marker in the edition content. When the lemma cannot be
matched (or the note has no ༼ at all, e.g. an editorial note), the span falls
back to the syllable before the marker, and the caller is told so.

Spans are half-open [start, end), absolute code-point offsets into the edition
content, and always non-empty: the API stores an empty span but never returns it.

The note text, of a durchen or a footnote, is a plain string. Nothing in it is
read as markup: a link, a transclusion, another [^label], a block ID or a
<small> tag inside a note is note text, sent as written. Only odd spaces are
normalised (normalize_spaces). The definition lines are taken out of the body
(split_definitions) before anything else reads it.
"""

from __future__ import annotations

import re

# A definition is a whole line: "[^label]: note". Anything else that looks
# like "[^label]" is a marker.
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]?(.*?)\s*$")
FOOTNOTE_MARK_RE = re.compile(r"\[\^([^\]\s]+)\]")

DURCHEN, FOOTNOTE, LEGACY = "durchen", "footnote", "legacy"
_LABEL_KINDS = ((re.compile(r"d[1-9]\d*"), DURCHEN), (re.compile(r"f[1-9]\d*"), FOOTNOTE),
                (re.compile(r"\d+"), LEGACY))


def kind(label):
    """What a footnote label marks: DURCHEN ([^d1]), FOOTNOTE ([^f1]), LEGACY (a
    bare number, the old form, still read as a durchen), or None for any other label."""
    for pattern, k in _LABEL_KINDS:
        if pattern.fullmatch(label):
            return k
    return None


def is_durchen(label):
    """True for the labels that become durchens: [^dN] and the old bare [^N]."""
    return kind(label) in (DURCHEN, LEGACY)

WITNESS_OPEN = "༼"     # ༼ opens the witness sigla; the lemma comes before it
TSHEG = "་"            # ་
TSHEG_NB = "༌"         # ༌ non-breaking tsheg, written before many markers
# Characters that end a syllable or a phrase: tsheg, shad and their variants.
PUNCT = set("་༌།༎༑༔")
# Whitespace, including the invisible characters the parsers treat as blank.
BLANK = set(" \t\r\n ​‌‍﻿")


# Characters that should not be in a source: spaces other than the plain space,
# invisible zero-width characters, and line breaks other than \n (\r\n is fine).
# The parsers normalise them (normalize_spaces) and the linters warn about them
# (odd_characters), so the source gets cleaned.
SPACE_LIKE = "    　" + "".join(map(chr, range(0x2000, 0x200b)))
INVISIBLE = "​‌‍⁠﻿­"
LINE_BREAKS = "  \u0085\u000b\u000c"
_CHAR_NAMES = {" ": "no-break space U+00A0", "​": "zero-width space U+200B",
               "‌": "zero-width non-joiner U+200C", "‍": "zero-width joiner U+200D",
               "⁠": "word joiner U+2060", "﻿": "BOM / zero-width no-break space U+FEFF",
               "­": "soft hyphen U+00AD", " ": "line separator U+2028",
               " ": "paragraph separator U+2029", "\u0085": "next line U+0085",
               "\u000b": "vertical tab", "\u000c": "form feed", "\r": "carriage return without \\n"}
_SPACE_MAP = str.maketrans({**{c: " " for c in SPACE_LIKE}, **{c: None for c in INVISIBLE}})

# FORK(liturgy-rails): in Indic scripts a ZWNJ/ZWJ right after a virama is
# orthography, not junk. Nepali writes र्‍य (eyelash ra, ZWJ) and भगवान्‌ले
# (visible halant, ZWNJ); dropping the joiner changes how the word renders.
# The Nepali translations carry 60 of them (26 notes), so those two stay; every
# other invisible character is still dropped (and still warned about).
VIRAMAS = "्্੍્୍்్್്්"
JOINERS = "‌‍"
_STRAY_JOINER_RE = re.compile(f"(?<![{VIRAMAS}])[{JOINERS}]")
_SPACE_MAP_KEEP_JOINERS = str.maketrans(
    {**{c: " " for c in SPACE_LIKE}, **{c: None for c in INVISIBLE if c not in JOINERS}})


def normalize_spaces(text):
    """Every space-like character becomes a plain space and invisible ones are
    dropped, except a ZWNJ/ZWJ right after a virama. Line breaks are left alone."""
    return _STRAY_JOINER_RE.sub("", text).translate(_SPACE_MAP_KEEP_JOINERS)


def char_name(c):
    return _CHAR_NAMES.get(c) or f"space U+{ord(c):04X}"


def odd_characters(text):
    """(line, name) for every character a source should not contain: a space
    other than the plain space, an invisible character, or a line break other
    than \\n / \\r\\n. Line numbers are 1-based in text."""
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if line.endswith("\r"):
            line = line[:-1]
        for i, c in enumerate(line):
            if c in JOINERS and i and line[i - 1] in VIRAMAS:
                continue  # FORK(liturgy-rails): Indic joiner, see VIRAMAS
            if c in SPACE_LIKE or c in INVISIBLE or c in LINE_BREAKS or c == "\r":
                out.append((n, char_name(c)))
    return out


def split_definitions(body):
    """Take the footnote definitions out of the body.

    Returns (body, defs, problems). Each definition line is replaced by an
    empty line, so line numbers and block boundaries stay where they were.
    defs maps label -> {"text", "line"} (1-based line in body). problems is a
    list of (level, message) for duplicate labels and blank notes. The text
    after "]: " is kept as a string; markers, links and block IDs in it are
    not read.
    """
    lines = body.split("\n")
    defs, problems = {}, []
    for i, line in enumerate(lines):
        m = FOOTNOTE_DEF_RE.match(line.rstrip("\r"))
        if not m:
            continue
        label, text = m.group(1), m.group(2)
        lines[i] = ""
        if label in defs:
            problems.append(("ERROR",
                f"footnote [^{label}]: defined twice (lines {defs[label]['line']} and {i + 1})"))
            continue
        text = normalize_spaces(text)
        if not text.strip():
            problems.append(("ERROR",
                f"footnote [^{label}] (line {i + 1}): empty note — the API needs some text"))
        defs[label] = {"text": text.strip(), "line": i + 1}
    return "\n".join(lines), defs, problems


def layout_problems(marked, defs):
    """Messages for a file whose labels break the layout: within each kind the
    markers number 1, 2, 3… in text order, and the definitions run d1…dK and
    then f1…fM. marked: labels in the order their first marker appears; defs:
    label -> {"line"}. Files still on the old bare numbers are not checked."""
    out = []
    for k, prefix in ((DURCHEN, "d"), (FOOTNOTE, "f")):
        got = list(dict.fromkeys(l for l in marked if kind(l) == k))
        want = [f"{prefix}{i}" for i in range(1, len(got) + 1)]
        if got != want:
            bad = next(i for i, (g, w) in enumerate(zip(got, want)) if g != w)
            out.append(f"[^{prefix}N] markers are not numbered 1, 2, 3… in text order: marker "
                       f"{bad + 1} is [^{got[bad]}], expected [^{want[bad]}]")
    order = [l for l, _ in sorted(defs.items(), key=lambda kv: kv[1]["line"])
             if kind(l) in (DURCHEN, FOOTNOTE)]
    want = sorted(order, key=lambda l: (kind(l) != DURCHEN, int(l[1:])))
    if order != want:
        out.append("footnote definitions are not grouped as d1…dK then f1…fM")
    return out


def lemma_of(note):
    """The base reading the note is about: the text before ༼, or None."""
    if WITNESS_OPEN not in note:
        return None
    lemma = note.split(WITNESS_OPEN, 1)[0].strip().rstrip("]").strip()
    return lemma or None


def _norm(c):
    return TSHEG if c == TSHEG_NB else c


def _span_end(content, end):
    """Where the span ends: after the trailing tsheg/shad before the marker,
    but not after trailing spaces ("ཏེ། [^1]" ends after ། )."""
    while end > 0 and content[end - 1] in BLANK:
        end -= 1
    return end


def _core_end(content, end):
    """End of the last letter before the marker (trailing tsheg, shad and
    spaces skipped)."""
    while end > 0 and (content[end - 1] in BLANK or content[end - 1] in PUNCT):
        end -= 1
    return end


def _match_lemma(content, core_end, lemma):
    """Start offset if the lemma ends at core_end, else None.

    Compared letter by letter going backwards, ignoring whitespace on both
    sides (the content joins verse lines with no separator) and treating ༌
    as ་.
    """
    want = [_norm(c) for c in lemma if c not in BLANK]
    while want and want[-1] in PUNCT:
        want.pop()
    if not want:
        return None
    i = core_end
    for c in reversed(want):
        while i > 0 and content[i - 1] in BLANK:
            i -= 1
        if i == 0 or _norm(content[i - 1]) != c:
            return None
        i -= 1
    return i


def _syllable_start(content, core_end):
    """Start of the syllable that ends at core_end."""
    i = core_end
    while i > 0 and content[i - 1] not in PUNCT and content[i - 1] not in BLANK:
        i -= 1
    return i


def locate(content, pos, note):
    """Span for a durchen whose marker sits at offset pos of content.

    Returns (start, end, how): how is "lemma" when the lemma matched,
    "no-lemma" when the note has no ༼ (fallback span), "mismatch" when the
    lemma did not match (fallback span), or None when there is no text before
    the marker at all (start and end are None too).
    """
    end = _span_end(content, pos)
    core = _core_end(content, pos)
    if core == 0:
        return None, None, None
    lemma = lemma_of(note)
    if lemma and all(c in PUNCT or c in BLANK for c in lemma):
        # A lemma of shad only ("།། །།"): the punctuation right before the marker.
        def bare(s):
            return [_norm(c) for c in s if c not in BLANK]
        if core < end and bare(content[core:end]) == bare(lemma):
            return core, end, "lemma"
    elif lemma:
        start = _match_lemma(content, core, lemma)
        if start is not None:
            return start, end, "lemma"
    return _syllable_start(content, core), end, ("mismatch" if lemma else "no-lemma")


def payload(durchens, content_length):
    """The POST bodies, sorted by span, after checking each against the API
    rules. durchens: dicts with start, end, text. Raises ValueError on the
    first bad one."""
    out = []
    for d in sorted(durchens, key=lambda d: (d["start"], d["end"])):
        start, end, text = d["start"], d["end"], d["text"]
        if not (isinstance(start, int) and isinstance(end, int)
                and not isinstance(start, bool) and not isinstance(end, bool)):
            raise ValueError(f"durchen span offsets must be integers: {d}")
        if not (0 <= start < end <= content_length):
            raise ValueError(f"durchen span out of range (content is {content_length}): {d}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"durchen text must be a non-blank string: {d}")
        out.append({"span": {"start": start, "end": end}, "text": text})
    return out


def footnote_payload(footnotes, content_length):
    """The footnote bodies, sorted by position (footnotes at the same position
    keep their marker order), after checking each one. footnotes: dicts with
    position, text. Raises ValueError on the first bad one."""
    out = []
    for f in sorted(footnotes, key=lambda f: f["position"]):
        position, text = f["position"], f["text"]
        if not isinstance(position, int) or isinstance(position, bool):
            raise ValueError(f"footnote position must be an integer: {f}")
        if not 0 <= position <= content_length:
            raise ValueError(f"footnote position out of range (content is {content_length}): {f}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"footnote text must be a non-blank string: {f}")
        out.append({"position": position, "text": text})
    return out
