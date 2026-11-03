# Removing the title from the content — live endpoint test

Run 2026-09-19 against `library.webuddhist.com` (OpenPecha API v2 2.11.2).
Question asked: can `PATCH /v2/editions/{id}/content` take the title out of the
content **and** take the `type: title` segment with it, or does the corpus have
to be re-uploaded? Answer: **it can — no re-upload is needed.**

## What was sent

There is no empty-string route — `ReplaceOperation.text` is `min_length=1`, so a
removal is a `delete` op over the title segment's own span:

```
PATCH /v2/editions/{edition_id}/content
{"type": "delete", "start": 0, "end": N}      # N = title segment's line-span end
```

Test text `ཁྱུང་པོའི་སྨོན་ལམ།` — the Tibetan root, plus its `en` and `zh`
translations. Three calls, all **204**. The other four translations
(`hi`, `ne`, `mn`, `vi`) were deliberately left alone as controls.

## Result

| edition | id | action | chars | segments | title segment | alignment pairs |
|---|---|---|---|---|---|---|
| `bo` | `0c1XHW3suAPaH4KSpmq0E` | **patched** | 1279 → 1256 | 8 → 7 | deleted | 6 → 6 |
| `en` | `3G9Xnkn2tzA0wI6iaFqlN` | **patched** | 1802 → 1774 | 8 → 7 | deleted | 8 → 7 |
| `zh` | `NlbBjxFiFFklpeWDFTLbQ` | **patched** | 346 → 341 | 8 → 7 | deleted | 8 → 7 |
| `hi` | `GQJ2SmCvISPxpIN9EuIC0` | left alone | 1411 → 1411 | 8 → 8 | still there (orphan) | 8 → 7 |
| `ne` | `DeO6sz8seI9CmA5vs4sBN` | left alone | 1401 → 1401 | 8 → 8 | still there (orphan) | 8 → 7 |
| `mn` | `yIpFUEYVrsdUdKiIGORmk` | left alone | 1522 → 1522 | 8 → 8 | still there (orphan) | 8 → 7 |
| `vi` | `vfGBdC8gvxgMqyTrm5IBs` | left alone | 1426 → 1426 | 8 → 8 | still there (orphan) | 8 → 7 |

Verified by re-fetching `…/content`, `…/segmentation/segments`, `…/alignments`
and every pair list, and diffing them against the snapshots taken first:

- **Content** — each patched edition lost exactly its title; the remainder is
  byte-identical; the title string occurs nowhere in the new content. The four
  control editions are unchanged.
- **Segments** — the `title` segment at reference `0` is **deleted outright**,
  not left as a zero-length stub. Every surviving segment keeps its **node id**,
  reference, type and line count, and its span shifts by exactly `-N`. So nothing
  was re-created: the segmentation was edited in place.
- **Alignments** — all six links on the root survive. Every pair list drops
  **only** `0->0 (title/title)`; the remaining seven pairs are byte-identical and
  still read `1->1 … 7->7`.

## Two things that decide how the sweep is written

1. **Patching the root alone already strips `^0` from all six pair lists**
   (measured after step 1, before the translations were touched), because the
   pair is a relationship hanging off the deleted segment. The four untouched
   translations were then left holding an orphan `title` segment aligned to
   nothing — which is why the sweep has to cover both sides.
2. **A second patch on the same edition would eat the next segment's text.**
   The sweep must `GET …/segmentation/segments?limit=1` and patch only when the
   first segment is `type: title` with `span.start == 0`. That check is also
   what makes the run resumable after an interruption.

## Mechanism (backend source, commit `cd1c205` — matches live 2.11.2)

`adjust_spans_for_delete` → `_adjust_span_for_delete` returns `None` for a span
the deleted range fully covers → the entity goes to `BATCH_DELETE_ENTITIES_QUERY`,
which is `DETACH DELETE span, localized, nomen, entity`. Alignment pairs are
`(:Segment)-[:ALIGNED_TO]->(:Segment)` relationships, so DETACH removes that
segment's pair and nothing else.

## Corpus shape this applies to

Surveyed all 94 root editions: **93 have exactly one `title` segment**, always at
reference `0`, always starting at character 0, span 10–115 chars. The exception is
`སྒྲོལ་མ་ཉེར་གཅིག་ལ་བསྟོད་པ།`, which is on the old `I-1` id scheme and has no title
segment. Translation editions have the same shape.

Sweep size: 93 roots + ~553 translation editions ≈ **646 PATCH calls**, one per
edition, each independent and idempotency-guarded by the first-segment check.

## The sweep (same day)

`4-SYSTEM/scripts/remove_title_segments.py` then ran over the whole corpus:
**646 editions cleaned** — 93 Tibetan roots and 553 translations. Independently
verified by `4-SYSTEM/scripts/verify_title_removal.py`, which compares the live
editions against the payloads that were uploaded in August:

```
editions checked: 646 | verified against payload: 645 | no payload on disk: 1 | problems: 1
alignment pair lists checked on 93 roots | lists still carrying a '0' reference: 0
```

The two exceptions are both pre-existing and unrelated to the sweep:
`རྟེན་འབྲེལ་བསྟོད་པ།` still carries the known stray `ˌ` (U+02CC) on the backend and
was re-segmented locally after upload (58 live segments vs 59 in the regenerated
payload), and its `-en` translation has no payload file on disk to compare with.

### One edition was damaged and repaired

The first version of the sweep retried a timed-out PATCH. **A content PATCH is
not idempotent**: the server had already applied it, so the retry deleted the
*next* 27 characters of edition `kIBLisYhjyIOw2NUno2Ul` (the `hi` translation of
`ཕ་དམ་པ་…དིང་རི་བརྒྱ་རྩ་མ`), losing `'स्वस्ति। दम्पा छरचेन ने दम्'`. The post-patch
check caught it as `verify-mismatch`. Repaired with an `insert` op of that text at
position 0, which restores the spans exactly as well — `_adjust_continuous_for_insert`
expands a span that starts at 0 and shifts the rest — and confirmed against the
payload: content equal, all 53 references, line counts and spans equal, 53
alignment pairs. The script now retries **GETs only**; a failed write is left for
the operator, because re-running the sweep re-reads the first segment and decides
again.

## State after the sweep

Every uploaded edition is title-free: no `type: title` segment anywhere, no `0`
reference in any alignment pair list. The titles still live on the text records,
and the table of contents — which is where the policy says heading titles belong —
has not been built yet.
