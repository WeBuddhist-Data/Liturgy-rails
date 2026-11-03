# Uploading the backlog outside the 94 (2026-09-21)

The 21 `0-INBOX` notes that had never been through the pipeline. **20 are now
live**; 1 is held back. This takes the liturgy category from 94 `bo` texts to
114 (113 from this vault + the tara21 text, which is not ours — see
`4-SYSTEM/retired-texts.txt`).

## 1. What was decided before any of it ran

Four calls, taken by the user, because stamping fixes the numbering and
renumbering after translation is expensive:

1. **Drop the repeated-title first body line** from the 7 notes that carried
   one. Under the 2026-09-20 rule that headings are TOC-only, that line would
   otherwise be content character 0 — precisely what the title sweep removed
   from 646 live editions two days earlier.
2. **Hold `ཐང་སྟོང་རྒྱལ་པོའི་གསུང་སྤྱན་རས་གཟིགས་ཀྱི་སྒོམ་བཟླས།` back.** Its
   frontmatter `title:` reads `…གསུང་སྐྱབས་འགྲོ་ནས་གཟིགས་ཀྱི་སྒོམ་བཟླས།` where
   its own H1 and its filename read `…གསུང་སྤྱན་རས་གཟིགས་ཀྱི་སྒོམ་བཟླས།`. The
   frontmatter value is what becomes the name in the library, so this is an
   expert's call, not a pipeline one.
3. **No TOC payload** — content and segmentation only, for these and for
   everything else. Block ids still use the hierarchical `^k-n` scheme, in
   which a segment's id derives from its section's id; only the *upload* of
   the TOC is deferred. `POST /v2/editions/{id}/table-of-contents` when wanted.
4. Nothing here touches the open block-id numbering question in
   `inbox-sync-report.md` §4 — that concerns the 93 already-live texts, where
   renumbering would destroy alignments. These 20 had nothing live to diverge
   from.

## 2. What was changed in the inbox

Two repairs, both approved, both to `0-INBOX` (the source of truth):

- the repeated-title body line dropped from 7 notes, with one adjacent blank
  line, so the H1 keeps exactly one separator;
- 18 stray block ids stripped from `ཀརྨ་པའི་གསོལ་འདེབས་…`. Ids live in
  `1-SOURCES`, never in the inbox — the same fix applied to
  `སློབ་དཔོན་ཐུགས་རྗེ་ཅན་…` on 2026-09-20.

No other character of any note was touched. The text, its segmentation and
its headings are the experts' and were left exactly as found.

## 3. Stamping

```bash
python3 4-SYSTEM/scripts/block_ids.py stamp "0-INBOX/<note>.md" --out-dir 1-SOURCES/Text
python3 4-SYSTEM/scripts/block_ids.py verify <dir> --src-dir 0-INBOX
```

20 stamped, 0 aborted. **`20 OK, 0 FAILED`** on verify, and independently
`strip(stamped) == inbox` byte for byte on all 20. `lint 1-SOURCES/Text` is
`113 OK, 0 FAILED`. 352 segments and 52 headings → 404 ids.

## 4. Three bugs found and fixed

These were live defects in the upload path, not cosmetic:

| where | what | consequence if unfixed |
| --- | --- | --- |
| `parser-root-text/parser.py` | `<small>` yigchung tags never stripped | raw `<small>` markup measured into `content` and into every character span |
| `liturgy_payloads.py` | `_headings` written into `edition.json` | a TOC-input key POSTed to the API as edition data |
| `upload_liturgy.py` | `WEBUDDHIST_APP` hard-required | upload impossible: the var is empty in `.env.local`, and the header is not in the v2 schema for these calls |

The parser change is recorded as fork #3 in `4-SYSTEM/scripts/UPSTREAM.md`. It
makes the parser agree with the backend rather than diverge: the live
`བཀའ་ཐང་བསྡུས་པ།` already stores its yigchung with the tags gone and the text
kept.

## 5. Two latent hazards found, deliberately not fixed

Both are worth knowing before the next stamping run:

- **`block_ids.py`'s `ID_RE` is digits-only** (`" \^\d+(?:-\d+)*$"`). An id
  containing a letter is invisible to both `is_stamped` and `strip_text`.
  `ཀརྨ་པའི་གསོལ་འདེབས་…` carried a hand-made `^a-0` / `^a-1-0` / `^a-1`
  scheme; `--restamp` left them in place and appended a second id beside each,
  producing `## མཇུག་བྱང་། ^a-0 ^2-0`. A note carrying *only* letter ids would
  be stamped a second time with no warning at all.
- **`_extract_blocks` does `block = raw.strip()`**, so leading whitespace on a
  block's *first* line is dropped while later lines keep theirs. One case in
  these 20 (` བམ་པོ་གཅིག་གོ །` in the Heart Sūtra), zero mid-block. Harmless
  here, but any independent prover must model it or it reports a false
  mismatch.

## 6. Proof

Before upload, every payload was re-derived straight from the stamped note
**without using the parser**, and compared: content string, segment count,
every `reference` against the note's own block id, every line span, no
`<small>`, no markdown, no heading text in content, spans tiling the content
from 0 to its length with no gap or overlap, no duplicate references, and no
stray keys anywhere in either payload. **All 20 pass.**

After upload, content and segments were read back from the live API for all
20 and compared to the local payloads. **All 20 match exactly** — 352
segments, 56,580 characters.

```bash
python3 4-SYSTEM/scripts/liturgy_payloads.py --category-id LCorCb2K98p3TICt3UCDm \
    --out-dir 4-SYSTEM/scripts/payloads-new-20 1-SOURCES/Text/<note>.md ...
python3 4-SYSTEM/scripts/upload_liturgy.py --payloads 4-SYSTEM/scripts/payloads-new-20
```

## 7. What is live

| text | chars | segs | text_id | edition_id |
| --- | --- | --- | --- | --- |
| `ཀརྨ་པའི་གསོལ་འདེབས་རྒྱལ་ཀུན་ཕྲིན་ལས་གཟུགས་ཅན་མ་བཞུགས་སོ། །` | 2180 | 14 | `YTiid1wrCCmxuz3XPNERU` | `QlntC2aOWNwWr5cfhWOfd` |
| `ཀླུ་བསངས་ནོར་བུ་དགོས་འདོད་ཀུན་འབྱུང་ཞེས་བྱ་བ་བཞུགས་སོ། །` | 1287 | 7 | `wvQkVlW6F9eIdUqMmxyLQ` | `AvYFPLsrazhTrHOYhihuL` |
| `གངས་ཅན་ཤིང་རྟ་ཉེར་ལྔའི་གསོལ་འདེབས། །` | 1219 | 8 | `R8j15brGl0BlqODzObHLR` | `Pvhg0eGKadScryJ37TmPc` |
| `གནས་བཅུའི་བསྟོད་པ་བཞུགས་སོ། །` | 9515 | 56 | `Bqib7701wAzY5QdhJkE9M` | `HbEdqd8k7ZgxA4iDvHZGm` |
| `གཙོ་རྒྱལ་མ་བཞུགས་སོ། །` | 1336 | 11 | `mshiNKICaTLa8TdYmvDbV` | `QBJsj8VI4MEJYDP503rWZ` |
| `གུ་རུ་ཐོལ་ཤགས།` | 5532 | 31 | `Ef4WgKDRFAhxlgWQvN4mx` | `Z3TIJPBUAhxmWNQDJD5DL` |
| `གོང་མ་ཁྲི་ཆེན་རིན་པོ་ཆེའི་ཞབས་བརྟན་བཞུགས་སོ།` | 309 | 2 | `UM9kxDXtRbzmxe5gZ24Mg` | `wl1piX22h2Mwu0vyjqJqX` |
| `དཔལ་༧རྒྱལ་དབང་འབྲུག་པ་བཅུ་གཉིས་པའི་ཞབས་བརྟན་བཞུགས་སོ།` | 182 | 1 | `vOOA0qT1pPgp3dWk4OaJo` | `clJ7g2sznODlrxDiKgzcQ` |
| `པོ་བསྟོད།` | 3417 | 22 | `dXDyUOufqDr7pb6viFT3k` | `9STF4uO1TtQBCZykGm9p9` |
| `བཅོམ་ལྡན་འདས་མ་ཤེས་རབ་ཀྱི་ཕ་རོལ་ཏུ་ཕྱིན་པའི་སྙིང་པོ།` | 3570 | 17 | `hrsOi9ekq1uhF9NhYuX3u` | `8TnFcGddeHDd6Zc0ICDgJ` |
| `མར་མེ་སྨོན་ལམ་གྱི་དབུ་ཕྱོགས་བྷི་ཧ་ར།།` | 5423 | 32 | `Wvl9EzF5FkIrKwgBUBwlg` | `pSsjbu97RBH7UsotuqIJh` |
| `ཚེ་དཔག་མེད་ཀྱི་གཟུངས།` | 2634 | 15 | `nNIfrB8f0X8UL0g66vgBe` | `IHrRYHlLOPz7FRWf9j7FO` |
| `འབྲི་གུང་༧སྐྱབས་མགོན་ཉི་ཟླ་གཟུང་གི་ཞབས་བརྟན་བཞུགས་སོ།` | 164 | 1 | `RfdzfxsnzsW8cmCm2isLl` | `oug5GRDfdG2AwoIrZ83pf` |
| `ཡིག་བརྒྱ།` | 264 | 1 | `IsVbiJhXWXi6Pd4fF1WvC` | `WNkwB4a3tzGeQ47qBOW0b` |
| `རྒྱལ་བ་ཡིད་བཞིན་ནོར་བུའི་ཞབས་བརྟན་བཞུགས་སོ།` | 152 | 1 | `NYzTpr40mMBrVYHz3a5Zt` | `FdZOSRht7catOnaJADuXO` |
| `རྗེ་བཙུན་སྒྲོལ་དཀར་གྱི་བསྟོད་པ་མཁྱེན་བརྩེ་དྲི་མེད་མ།` | 3577 | 24 | `yjEQEUflvc2yxInNVOWDa` | `H2HYOjaGfJtdO5jvvHytp` |
| `རྡོ་རྗེ་སེམས་དཔའི་མཚན་སྔགས།` | 753 | 5 | `HiOqbffD2n7GoeZ5IEFGc` | `VyV8a9iPtNaKo58l8O3Z0` |
| `རྣམ་དག་བདེ་ཆེན་ཞིང་གི་སྨོན་ལམ་བཞུགས་སོ།` | 13560 | 92 | `l96Qa5CyPGLZFVhjJg5qC` | `NednJSN6bvfYrl6ABCMkM` |
| `རྣམ་དག་བདེ་ཆེན་ཞིང་གི་སྨོན་ལམ་རྣམ་གྲོལ་མ་བཞུགས་སོ།` | 1162 | 10 | `5SCUJjNVR4TmD7ZF544Rj` | `HUzDdcroc3jxRIIefYkho` |
| `སངས་རྒྱས་སྨན་བླའི་གཟུངས།` | 344 | 2 | `ZqK8oglmf3FiBtLocskRb` | `dcyqoCYoRXWgvbw1TN9gS` |

## 8. What is still open

- **Translations.** None of the 20 has any. They sit in
  `5-REVIEW/not-done/1-upload-incomplete/`. English and Chinese come from the
  `dharmamitra-translate` skill, Hindi/Nepali/Mongolian/Vietnamese from
  `gemini-translate`. Mind the DharmaMitra limit of 400 requests per day.
- **`ཐང་སྟོང་རྒྱལ་པོའི་གསུང་…`** — needs the title resolved, then it stamps
  and uploads like any other.
- **The `status:` field on the 20 inbox notes.** They still read `segmented`,
  `Finalised` or `Fanilised`. The existing value for "uploaded" is
  `TOC added`, which is now the wrong words for a text uploaded with no TOC.
  Needs a word from the user before re-marking.
- **`Fanilised` is a typo for `Finalised`**, on 7 notes. It is the data team's
  field, so it was left alone.
- **Author links.** 6 of the 20 name an author in frontmatter, none resolved
  to a backend person id, so `contributions` was omitted rather than guessed.
  `stamp_metadata.py --authors` can link them later.
- **Nothing in this run is committed.** The working tree also still carries
  the uncommitted 2026-09-19 title sweep and 2026-09-20 inbox sync.

## Moved out: the Heart Sūtra (2026-10-04)

`བཅོམ་ལྡན་འདས་མ་ཤེས་རབ་ཀྱི་ཕ་རོལ་ཏུ་ཕྱིན་པའི་སྙིང་པོ།` (library text `hrsOi9ekq1uhF9NhYuX3u`, edition `8TnFcGddeHDd6Zc0ICDgJ`) no longer lives in this vault.
Its library copy was deleted and replaced by the heart-sutra-rails upload: Sanskrit root `j0aFMqWUNkfR99ekbFf4T`, Tibetan translation `bt0xXVpb3CcXI6cYqTr8n` (category Prajñāpāramitā), with Chinese, English and three commentaries attached. The Chants and Vajrayāna tags moved to that root.
This vault's note, inbox copy, review folder, payloads and ledger entry were moved to `heart-sutra-rails/0-INBOX/raw-data/liturgy-rails-copy/`, and a snapshot of the deleted library copy is at `heart-sutra-rails/0-INBOX/raw-data/liturgy-copy-snapshot-hrsOi9ekq1uhF9NhYuX3u.json`.
