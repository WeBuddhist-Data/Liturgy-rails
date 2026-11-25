# Parser — Root Text

Takes a source file and its linter output and writes the API payloads for the text, edition, table of contents and (for translations and commentaries) alignment.

Run `linter-root-text` first.

## What it does

1. **extract_text_input** — takes `text_input` from the lint JSON (or `resolved` from a `.lint.errors.json`), drops empty fields and contributors without an id, writes `text.json` (includes `tag_ids`, a list, when set). A translation (`translation_of` set) is sent without `tag_ids`: it inherits the root text's tags, and the API refuses tags on it (HTTP 404 "Work … or Tag … not found")
2. **build_edition** — builds the edition content and its segments with character spans from the body text (headings left out), writes `edition.json`, the yigchung annotations found in `<small>…</small>`, writes `yigchungs.json`, and the durchens (variant readings) written as footnotes, writes `durchens.json`
3. **build_toc** — builds a nested table of contents from the headings (headings are used only here), writes `toc.json`
4. **build_alignment** — for `file_type` `translation` or `commentary` only, links segments to the root text through transclusions, writes `alignment.json`

## Output

```
output/
  <stem>/                  # one folder per source file, named after it
    <stem>.text.json        # text_input payload
    <stem>.edition.json     # edition metadata, content and segments
    <stem>.yigchungs.json   # yigchung (small-script) annotation spans — only when the text has <small>
    <stem>.durchens.json    # durchen (variant-reading) notes with their spans
    <stem>.footnotes.json   # footnotes [^fN] with their positions — only when the text has some
    <stem>.toc.json         # nested TOC with character spans
    <stem>.alignment.json   # translation/commentary → root-text alignments
```

## Edition

- `metadata.type` comes from `edition_type` (default `critical`); `metadata.source` from `source` or `source_url`
- **Headings are not part of the edition.** They add no segment and no text to `content`; they are used only for the table of contents
- Blocks are separated by blank lines. Each content block becomes one segment, with one span per line, and its block ID (without `^`) as `reference`
- The block ID is removed from the text; lines are joined with no separator
- Blocks without a block ID are skipped with a warning (headings too); so are content blocks whose ID has more than 3 parts
- Inline formatting for interlinear glosses (`<small>…</small>`) is dropped from `content`: the gloss text stays, the tags do not, and each run becomes a yigchung annotation (see below). The source file is never changed.
- Non-breaking spaces (U+00A0) become ordinary spaces in `content`.
- Transclusion lines (`![[...#^ref]]`) are left out of the content

### Segment types

The shape of the block is checked first, then its ID:

| Test | Type | Example |
|------|------|---------|
| **Two or more lines with no empty line between them** | `verse` | a quoted verse, one line per unit |
| ID starts with `T` or `t` | `top_segment` | `^T-1` |
| Any part is an uppercase Roman numeral | `front_matter` | `^I-1`, `^2-I-3` |
| Any part is lowercase letters only | `back_matter` | `^a-1` |
| Anything else | the document default | `^1-1`, `^1-2x3`, `^1-U4` |

**Verse wins over the ID.** A verse in the front matter or the colophon is `verse`, not `front_matter` or `back_matter`: those mark where a block sits, and the block's shape says what it is. The ID types only blocks that are not verse.

The document default is `verse`. It is `paragraph` when the file has `commentary_of`, or is a translation whose `root_text` is a commentary.

An empty line inside a block — including a line holding only an invisible character such as a zero-width space — stops it being verse, and the parser warns about it. Such a block is usually two paragraphs that each need their own block ID, and Obsidian shows no gap there, so the warning is the only sign.

A block of prose that was hard-wrapped onto several lines without a blank line between them reads as verse to this rule. Keep a paragraph on one line.

## Yigchungs

Yigchung (ཡིག་ཆུང་, small-script gloss) is written `<small>…</small>` in the source. `yigchungs.json` holds one item per unbroken run of yigchung text, sorted by `start`. Each item is exactly the body of `POST /v2/editions/{edition_id}/yigchungs`:

```json
{"yigchungs": [{"span": {"start": 550, "end": 557}}, {"span": {"start": 566, "end": 583}}]}
```

- Spans are half-open `[start, end)`, **absolute offsets into the edition `content`** (not relative to a segment or line), counted in Unicode code points (Python `len()`), so a Tibetan stack is several units
- Two runs in one line are two items, never one span covering the plain text between them
- A run that continues onto the next line of the same block is one item. So is `</small><small>` with nothing between: it is one unbroken stretch
- Empty runs (`<small></small>`) are left out: the API accepts a zero-length span but never returns it
- A run never carries past the end of its block
- `<small>` in a heading: the tags are dropped from the TOC title, and no yigchung is recorded, since headings are not edition content (warning)
- Stray tags (a `<small>` not closed by the end of its block or heading, a `</small>` with no open run, a `<small>` inside an open run) are caught by the **linter**, which lists each one as an error. The parser keeps a safety stop, since it also accepts a `.lint.errors.json`: on a stray tag it names the first one, writes no `edition.json` or `yigchungs.json`, and exits with status 1. Tags are matched case-insensitively
- **No file when there are no runs**: a text without `<small>` gets no `yigchungs.json`, and one left from an earlier run is deleted, so a file that loses its markup does not leave a stale payload
- The API does not check for duplicates: before re-uploading an edition that already has yigchungs, delete or check the existing marks. `PATCH /v2/editions/{id}/content` shifts existing marks itself

Worked example: `སྒྲོལ་མ་<small>ལྗང་མོ་</small>འཁོར་བཅས་<small>ཉི་ཤུ་རྩ་གཅིག་པོ་</small>གཤེགས་སུ་གསོལ། ། ^II-2` gives the line `སྒྲོལ་མ་ལྗང་མོ་འཁོར་བཅས་ཉི་ཤུ་རྩ་གཅིག་པོ་གཤེགས་སུ་གསོལ། །` (57 code points) and two yigchungs at `[8,15)` ལྗང་མོ་ and `[24,41)` ཉི་ཤུ་རྩ་གཅིག་པོ་ relative to that line; the payload holds them plus the line's start in the content.

## Durchens

A durchen (variant-reading note) is written as an Obsidian footnote: a marker right after the text it is about, and a definition at the end of the file.

```
རྣམ་དག་ཆོས་ཀྱི་དབྱིངས་ལ༌[^1]རོལ་མཛད་ཅིང་། །

[^1]: ལ་ ༼སྣར། པེ།༽ ལས་
```

The note reads `<lemma> ༼<witness sigla>༽ <variant>`. `durchens.json` holds one item per marker, sorted by span. Each item is exactly the body of `POST /v2/editions/{edition_id}/durchens`, which takes **one object per request**, so post the items one at a time:

```json
{"durchens": [{"span": {"start": 412, "end": 414}, "text": "ལ་ ༼སྣར། པེ།༽ ལས་"}]}
```

- **No file when there are no durchens**: a text without durchen notes gets no `durchens.json`, and one left from an earlier run is deleted, so a file that loses its notes does not leave a stale payload
- The marker is dropped from `content`, and the definition lines are not content at all
- `text` is the whole note, as written: a plain string. A link, a transclusion, another `[^label]`, a block ID or a `<small>` tag inside a note is note text, never read as markup. The definition lines are taken out before anything else (content, TOC, alignment) reads the file
- **The span is the lemma**: the text before `༼`, matched backwards from the marker. Matching ignores whitespace, because verse lines are joined with no separator, and treats `༌` as `་`. Trailing tsheg and shad are inside the span, and trailing spaces are not. In the example above the span is `ལ༌`
- A lemma of shad only (`།། །།`) spans the shad just before the marker
- **Fallback (warning):** when the lemma does not end at the marker, or the note has no `༼` (an editorial note), the span is the syllable just before the marker. The marker position is certain, but the start of the span is then a guess, so check those notes
- Spans are half-open `[start, end)` absolute code-point offsets into `content`, like yigchungs, and never empty: the API stores an empty span but never returns it
- Markers left on their own after a block (`…། །` followed by a blank line and `[^5]`) mark the end of the text before them
- A marker in a heading, or in a block that is skipped, gets no durchen (warning). So does a marker with no definition, or with an empty note
- Every payload is checked against the API rules before it is written: integer offsets, `start < end <= len(content)`, non-blank `text`, and no keys besides `span` and `text`. `metadata` is never sent, since the API never returns it
- The file is written even when there are no footnotes (`{"durchens": []}`)
- The API does not check for duplicates: before re-uploading an edition, delete or check its existing durchens

The span logic is in `durchen.py`. The same file sits in `linter-root-text`, `linter-commentary`, `parser-root-text` and `parser-commentary`: change one, copy it to the other three.

## Footnotes

The footnote label says what a footnote is: `[^dN]` is a durchen, `[^fN]` a footnote (an editorial remark, a source citation, a colophon printed as a footnote). The old bare `[^N]` is still read as a durchen. At the end of the file the durchen definitions come first, `d1…dK`, then the footnotes, `f1…fM`.

A footnote has a **position**, not a span: the gap between two characters, from `0` (before the first character) to `len(content)` (after the last). The marker goes right before `content[position]`. For `ཀཁག`: `0` → `●ཀཁག`, `2` → `ཀཁ●ག`, `3` → `ཀཁག●`. Positions count Unicode code points, and adding a footnote never changes the content.

```
གསུང་ངོ་།།[^f1]  །། ^1-6

[^f1]: ཆོས་ཚན་འདི་ཉིད་ནང་། …
```

```json
{"footnotes": [{"position": 268, "text": "ཆོས་ཚན་འདི་ཉིད་ནང་། …"}]}
```

- The position is where the marker stood once the markers are taken out of `content`; there is no lemma to match
- Sorted by position; footnotes at the same position keep their marker order
- A marker at the end of a line sits at the end of that line's text, which is also the start of the next line's (lines are joined with no separator). A marker after the last text of a block, or on its own after it, sits at the end of the text before it
- A marker in a heading, or in a block that is skipped, gets no footnote (warning). So does a marker with no definition, or with an empty note
- Checked before writing: integer `position`, `0 <= position <= len(content)`, non-blank `text`
- **No file when there are no footnotes**, and one left from an earlier run is deleted
- There is no API endpoint for footnotes yet: nothing uploads `footnotes.json`

## Table of contents

- Built from the headings; the heading level (`#` count) sets the nesting
- Spans are character offsets into the edition `content`, which has no heading text. A section starts where the text after its heading starts, and ends where the next heading at the same or a higher level starts (or at the end of the content)
- A heading with no text before the next heading gets an empty span (`start` = `end`)
- Headings deeper than level 6 (7+ `#`) are often written in bold, since Obsidian renders only six levels. For those, the `**` markers are dropped from the title.
- Titles are keyed by `lang_tag` (default `en`); Tibetan titles in Wylie are converted to Unicode

## Alignment

Transclusions (`![[<root file>#^ref]]`) link segments of a translation or commentary to segments of the root text.

- `source_segment_reference` — segment in this file
- `target_segment_reference` — segment in the root text

Transclusions in blocks without a block ID are collected and attached to the next block that has one, together with any transclusions in that block. The list then starts over. A group of transclusions before one block therefore gives a many-to-one alignment.

Headings are never aligned, and waiting transclusions are dropped when a heading comes before the next content block. Transclusions that point to a heading in the `root_text` file are skipped with a warning, since headings are not segments.

## Requirements

```
pip install PyYAML pyewts
```

Python 3.8+.

## How to run

Run from the vault root (the folder that contains `1-SOURCES/` and `4-SYSTEM/`):

```bash
python3 4-SYSTEM\scripts\parser-root-text\parser.py "1-SOURCES\Text\<lang>-<title>.md" "4-SYSTEM\scripts\linter-root-text\output\<lang>-<title>.lint.json"
```

## Notes

- If the lint JSON has no alt titles or contributors, the parser warns and carries on
