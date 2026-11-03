# Syncing 0-INBOX → 1-SOURCES → the live library (2026-09-20)

Written after the title-removal sweep, under two rules set by the user:

1. **Heading lines (`#`, `##`, `###`) are table-of-contents material, never
   content.** They do not enter the edition's content string.
2. **`<small>…</small>` marks a yigchung (ཡིག་ཆུང) annotation.** The tags are
   not content; the text between them is. This is already how the live
   backend stores `བཀའ་ཐང་བསྡུས་པ།`.

The TOC itself is **not** being pushed yet, and the block-id numbering
scheme is deferred for review — see §4.

## 1. What was done to the vault

`0-INBOX` (five approved fixes, restricted to the 93 uploaded texts):

- dropped the repeated-title first body line from the 5 notes that still had
  it, the same edit the experts had already applied to the other 88;
- stripped 10 stray block ids from `སློབ་དཔོན་ཐུགས་རྗེ་ཅན་…` (ids live in
  `1-SOURCES`, never in the inbox);
- inserted the missing space in `##མཇུག་བྱང་།` in `གཏོར་མ་ཆ་གསུམ་བཞུགས་སོ།`
  — without it the line is not a heading to any parser, which contradicts
  the expert's evident intent and made the stamper refuse the file.

`1-SOURCES/Text` — **all 93 bodies are now byte-identical to their inbox
note**, headings included, stamped with block ids. 28 were already identical,
65 were rewritten. Each note's own frontmatter was kept verbatim, so
`text_id`, `edition_id`, `title_en/zh/hi/ne/mn/vi`, `contributor_status` and
the resolved `[person:…]` author tags all survive. Verified independently
afterwards: 93/93 bodies equal the inbox, 93/93 still carry `text_id` and
`title_en`.

One frontmatter difference was deliberately **not** copied: the inbox `title:`
of `སྤྱོད་འཇུག་སྨོན་ལམ་བཞུགས་སོ།` has `དམིགས་བརྩེ་མ།` glued in front of it — a
paste error, not a retitle.

## 2. What the 93 live editions need

| | texts | requests |
|---|---|---|
| **Nothing to change** — content and segmentation already correct | **86** | 0 |
| **Content patch** — segmentation intact, all alignments survive | **4** | 94 |
| **Re-segmentation** — boundaries move, alignments die | **3** | ~27 |

### The 4 patchable texts

| text | ops | what changed |
|---|---|---|
| `གསོལ་འདེབས་ལེའུ་བདུན་མ།` | 13 | `༴` shorthand expanded to `་གསོལ་བ་འདེབས༔` |
| `ཇོ་བོ་རྗེའི་བསྟོད་པ་ཕུན་སུམ་ཚོགས་པ་མ` | 38 | `༑` → `།` |
| `དགེ་སློང་མ་དཔལ་མོས་…` | 42 | `།།` → `། །`, plus a 19-char title remnant still at position 0 on the backend |
| `བཀའ་ཐང་བསྡུས་པ།` | 1 | a 9-char typo fix |

Payloads: `4-SYSTEM/scripts/payloads-live-patch/`. Each was proved twice —
the backend's own span arithmetic replayed over the live spans reproduces the
new spans and the new segment shape exactly, and the op list replayed against
a **fresh** read of the live content lands on the new content char for char.

### The 3 re-segmentations

- `གསོལ་འདེབས་བར་ཆད་ལམ་སེལ།` — text identical; the expert split the last
  10-line block into 8 verse lines + 2 mantra lines (24 → 25 segments).
  6 alignments; one translation block splits in each of the 6 languages.
- `དཔལ་ས་སྐྱ་པའི་བསྟན་པ་…` — a stray `་` alone on line 158, directly under
  `### མཛད་བྱང་།`. Almost certainly a typo. **Remove it and this text needs
  nothing at all.**
- `རྟེན་འབྲེལ་བསྟོད་པ།` — the known broken one (0 alignments). The stray `ˌ`
  (U+02CC) glued two verses into one 9-line segment on the backend; the
  inbox wants them separate. Repair with its other outstanding work.

## 3. Translations

Under a patch the segment references are untouched, so all six alignments
survive and **no retranslation is needed** for the 4 patchable texts. Two
optional touch-ups: the 12 segments of `གསོལ་འདེབས་ལེའུ་བདུན་མ།` where `༴`
became real words now say more than the existing translation does, and the
9-char fix in `བཀའ་ཐང་བསྡུས་པ།` changes one phrase.

Real translation work is confined to `གསོལ་འདེབས་བར་ཆད་ལམ་སེལ།`: one block
splits into two, in 6 languages.

## 4. Open: the block-id numbering scheme

`block_ids.py` numbers segments `^0-n` / `^k-n` as soon as a `##` appears.
Because 54 texts gained colophon headings, the ids now in `1-SOURCES`
diverge from the references stored on the backend:

```
37 match      (the texts with no ## heading at all)
54 diverge    flat 1..N  ->  0-n / k-n, although not one character moved
 2 diverge    genuinely different segment counts (the re-segmentations)
```

Example — `གཏོར་མ་ཆ་གསུམ་བཞུགས་སོ།`:
`live ['1','2','3',…,'47']` vs `new ['0-1','0-2','0-3',…,'1-1-1']`.

Nothing has been pushed on the strength of these ids. The choice:

- **Number straight through regardless of headings** (`^1..^N`). Headings are
  TOC-only now, so they need not drive numbering. The ids then match the
  live references for 91 of 93 texts, nothing renumbers, and no translation
  track needs touching.
- **Keep the current scheme.** 54 texts renumber although no character moved.
  Each needs `DELETE segmentation` + `POST segmentation` + 6 alignment
  re-PUTs — roughly 464 extra requests, every alignment on those texts
  destroyed and rebuilt, and all six translation tracks renumbered to match.

## 5. What the backend cannot do

- **No `PUT …/segmentation`.** A split, merge or renumber is `DELETE` + `POST`,
  which silently destroys every alignment on the edition.
- **No `PATCH /v2/editions/{id}`.** Edition metadata (`source`, `type`,
  `colophon`, `incipit_title`) cannot be changed at all.
- **No batched content ops and no `If-Match`.** One operation per request,
  each shifting the offsets of the next, with no rollback half-way.
- **A content PATCH is not idempotent** — a retried op eats the next N
  characters. Retry GETs only, never a write.
- **No `PUT …/table-of-contents`** — delete + add only. Moot: all 93 editions
  currently have zero TOCs.

For the TOC when it is wanted: `POST /v2/editions/{id}/table-of-contents`
takes nested sections, each with a `LocalizedString` title and a character
span, and validates only that the largest `end` fits inside the content. The
`Span` schema explicitly supports "a heading with no content of its own" via
an empty range — exactly rule 1 above. 54 texts have `##` sections; the other
39 carry only an H1 and need no TOC.

## 6. Reproducing this

```bash
python3 4-SYSTEM/scripts/fetch_live_editions.py 4-SYSTEM/scripts/live-editions.json
python3 4-SYSTEM/scripts/live_patch_payloads.py
```

`new_content_rules.py` holds the two content rules; `live_patch_payloads.py`
builds and proves the payloads. Both are read-only.
