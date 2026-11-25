# Upstream

| | |
|---|---|
| Source repository | https://github.com/WeBuddhist/Nalanda-texts-rails |
| Path | `4-SYSTEM/scripts/parser-root-text/` |
| Pinned commit | `3ec0e1bc` (2026-11-03, "Parsers: note text is a plain string") |
| Copied | 2026-11-03 |
| Origin | https://github.com/WeBuddhist/21-taras-rails, commit `2d14cddf9bdd207e34da4eff58b1d0fa98141a5f` (2026-10-01, "Add support for yigchungs in linter and parser"), vendored into Nalanda-texts-rails on 2026-10-12 |

Copied byte-identical, without the `output/` folder. Every later commit here is a template delta.

**Why vendored:** every vault turns its linted root texts into the API payloads (text, edition, TOC, durchens, footnotes).

The parser writes to `output/<stem>/` beside itself, not to `0-INBOX/`. That is how upstream works, and it is kept so a re-sync stays a plain diff.

## Changes made in Nalanda-texts-rails (before the copy)

- 2026-10-12: **durchens** (variant readings written as footnotes). Added `durchen.py`, shared by all four tools. The parser strips markers from `content` and TOC titles and writes `<stem>.durchens.json`. Offer it back to 21-taras-rails.
- 2026-10-26: **footnote types.** Labels say what a footnote is: [^dN] durchen, [^nN] generic note; a bare [^N] is still read as a durchen until the sources are relabelled. durchen.py gained kind(), is_durchen() and layout_problems().
- 2026-10-29: **footnotes by position.** Generic notes are now footnotes, `[^fN]` (was `[^nN]`). A footnote has a position, not a span: the gap before `content[position]`, 0…len(content), where the marker stood. The parser writes `<stem>.footnotes.json` (`{"footnotes": [{"position", "text"}]}`) when a text has footnotes; nothing uploads it yet. durchen.py: `NOTE` → `FOOTNOTE`, new `footnote_payload()`.
- 2026-10-26: **odd spaces.** Edition content, TOC titles and durchen texts go through `durchen.normalize_spaces()`: space-like characters (U+00A0, U+2000–U+200A, U+202F, U+205F, U+3000, U+1680) become plain spaces and invisible ones (U+200B–U+200D, U+2060, U+FEFF, U+00AD) are dropped, piece by piece so marker offsets stay right. Before, only U+00A0 in the content was handled.
- 2026-11-03: **note text is a plain string.** Alignment and the root-text heading lookup take the footnote definitions out first, as the edition already did, so a transclusion or block ID inside a note never becomes an alignment or a segment reference. Same change in parser-commentary.

## Template changes

None yet.
