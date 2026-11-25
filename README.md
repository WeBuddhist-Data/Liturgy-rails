# Liturgy-rails

ཞལ་འདོན་ཕྱོགས་བསྒྲིགས། · Tibetan liturgy · the 100 most-recited texts

An [Obsidian](https://obsidian.md) vault of the 100 most widely recited
Tibetan liturgy texts — prayers, praises, aspirations, dhāraṇīs and ritual
modules — prepared for upload to the WeBuddhist library and as the base for
translation, adaptation and practice-plan work.

## Windows users: enable long paths first

Some files in this repo have paths longer than Windows' 260-character limit
(the parser output under `4-SYSTEM/scripts/parser-root-text/output/` is named
after each text's full Tibetan title, twice). Every Windows user needs to run
this once, before cloning or pulling — otherwise Git (and Obsidian Git) stops
with `Filename too long`:

```powershell
git config --global core.longpaths true
```

## Why a rails vault

A liturgy corpus has a problem a single-treatise vault does not: it is 100
short, independent, *performed* texts, each with its own tradition,
function and occasion. Ask a model to translate or adapt one cold and it
has no idea whether it is handling a refuge formula, a protector torma
offering, or an aspiration recited only on the tenth day — so it produces
something fluent and liturgically wrong.

The rails methodology separates two jobs. `2-RAILS/` is the
source-language specialist made permanent: what each text is, who recites
it, what its ritual function is, what its recurring formulae mean — compiled
once, cited to sources. Each track in `3-TRANSFORMATIONS/` is a
target-language specialist for one audience, bound by its own style and
vocabulary contract. Lay the rails once; run many transformations on them.

## Structure

```
0-INBOX/   →   1-SOURCES/   →   2-RAILS/   →   3-TRANSFORMATIONS/
raw            prepared         descriptive     per-output prescriptive
intake         source notes     context         rails + generated output

                       ▲
                       │ all driven by
                       │
                    4-SYSTEM/   scripts, docs, templates
```

Each folder's `About <Folder>.md` is the authoritative document for what
goes in it.

- **[`0-INBOX/`](0-INBOX/)** — verbatim instance content as fetched from the
  backend, one file per text. Kept so any formatting decision can be
  checked without re-fetching.
- **[`1-SOURCES/`](1-SOURCES/)** — the 100 prepared notes (`Text/`) plus
  `liturgy-catalog.json`, the registry of rank, backend ids and per-text
  metadata. Ground truth; no interpretation.
- **[`2-RAILS/`](2-RAILS/)** — *not yet built.* Per-text packages (tradition,
  ritual function, occasion), section notes for long rites, a local wiki of
  recurring liturgical formulae, bilingual glossaries.
- **[`3-TRANSFORMATIONS/`](3-TRANSFORMATIONS/)** — *not yet built.*
  Translations, adaptations (practice booklets, phonetic editions), and
  calendar-paced plans.
- **[`4-SYSTEM/`](4-SYSTEM/)** — the scripts, plus
  [`CLAUDE.md`](4-SYSTEM/CLAUDE.md), the LLM-facing operational summary.

## Provenance

OpenPecha category `dJpr4gMF72E4UpCnJ84sh` (Tibetan, critical instances),
fetched from `https://api-aq25662yyq-uc.a.run.app`. The corpus of 205 was
narrowed to the 100 most-recited using the backend's own recitation
ranking, matched by normalised title; `1-SOURCES/liturgy-catalog.json`
preserves that rank.

**Every text already exists in OpenPecha** — its `text_id` and
`instance_id` are in the catalog, not in the notes. Uploading edited
content updates the existing instance rather than creating a new text.

## What a source note contains

Metadata (frontmatter), the TOC (headings), and the segmentation
(blank-line-separated blocks) — nothing else. No `^block-id` anchors: the
v2 API expresses segmentation as character spans, so the block boundary is
the segmentation. Metadata values come from the backend where present and
are left empty where not; `category_id` and `date` are empty by policy
(see [`1-SOURCES/About Sources.md`](1-SOURCES/About%20Sources.md) §5).

## Building the upload payloads

```bash
python3 4-SYSTEM/scripts/build_payloads.py --category-id <CATEGORY_ID>
```

Writes `text.json` / `instance.json` / `toc.json` per note into
`4-SYSTEM/scripts/output/`, matching `POST /v2/texts`,
`POST /v2/texts/{text_id}/instances` and
`POST /v2/annotations/{instance_id}/annotation`. Every span is asserted to
slice back to exactly its source block. 100 notes → 4,192 segments.

Note: the pipeline repo's `tools/parser/parser.py` cannot read these notes
— it derives spans from block IDs, which this format omits by design.
`build_payloads.py` is the parser for this format.

## Current state

| | |
|---|---|
| Texts | 100 — 88 verse layout, 12 prose layout |
| `license` / `bdrc_work_id` / `author` | 100 / 85 / 32 filled |
| Payloads | build clean; awaiting a `category_id` |
| Rails, transformations | scaffolding only |

The 12 prose-layout texts (mixed metre, mantra-interleaved rites) have the
least certain segment boundaries and are the review queue before upload.
