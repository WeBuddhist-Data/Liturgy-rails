# CLAUDE.md — agent entry point

Operational summary for an LLM working in this vault. The authoritative
documents are each folder's `About <Folder>.md`; read the relevant one
before touching that folder.

## 1. What this vault is

An Obsidian vault of the **100 most-recited Tibetan liturgy texts**,
ingested from the OpenPecha backend and prepared for upload to the
WeBuddhist library. It follows the four-stage rails layout:

```
0-INBOX/   →   1-SOURCES/   →   2-RAILS/   →   3-TRANSFORMATIONS/
raw            prepared         descriptive     per-output
intake         source notes     context         prescriptive rails
                                                + generated output
                       ▲
                       │ all driven by
                     4-SYSTEM/   scripts, docs, templates
```

Current state: `0-INBOX/` and `1-SOURCES/` are populated and upload-ready.
`2-RAILS/` and `3-TRANSFORMATIONS/` are scaffolding — read their `About`
files before laying the first rail or track.

## 2. The load-bearing facts

**Notes carry block IDs, and they are load-bearing.** (This paragraph said
the opposite until 2026-08-31; it predated the block-ID migration.)
`1-SOURCES/Text/*.md` carry metadata frontmatter, TOC headings,
blank-line-separated blocks, and an Obsidian `^N` anchor closing every block.
The anchors are stamped by `4-SYSTEM/scripts/block_ids.py` from the
expert-segmented notes in `0-INBOX/` — never by hand, and never by an agent.

They are not decoration. Each `edition.json` carries
`segmentation.segments[].reference`, and that reference *is* the block id, so
the anchors are what the v2 API's character spans are derived from. They are
also the alignment between a Tibetan block and its translation: block `^N` in
`3-TRANSFORMATIONS/Translations/…` renders source block `^N`. **Stripping them
silently destroys both the upload segmentation and every translation
alignment.**

**Consequence:** the parser reads these notes *because* they carry ids —
`parser-root-text/parser.py` matches `^N` to find block boundaries. A note
without ids is what returns zero segments. `build_payloads.py` is the older
ID-free path; see the note in §3.

**Empty metadata values are deliberate.** A key with no value means the
backend does not record one. Never fill it with a guess. Two are empty by
policy: `category_id` (the WeBuddhist target category is not yet chosen)
and `date` (the backend's `date` is an ingestion timestamp, not a
composition date).

**These texts already exist in the backend.** Their `text_id` and
`instance_id` live in `1-SOURCES/liturgy-catalog.json`, deliberately not
in the notes. Uploading edited content means **updating the existing
instance** (`PUT /v2/instances/{instance_id}`), not creating a new text.

## 3. Scripts

Both run from the vault root.

```bash
# rebuild 0-INBOX/ + 1-SOURCES/ from the fetch artifacts (rarely needed)
python3 4-SYSTEM/scripts/format_liturgy_batch.py \
    <work_list.json> <contents.json> <text_meta_all.json> [ranking.json]

# notes → OpenPecha v2 API payloads, into 4-SYSTEM/scripts/output/
python3 4-SYSTEM/scripts/build_payloads.py --category-id <CATEGORY_ID>
```

> **Stale (2026-08-31):** this describes the older ID-free path, which wrote
> `instance.json`/`toc.json` into `4-SYSTEM/scripts/output/`. What is actually
> uploaded today is `<stem>.text.json` + `<stem>.edition.json`, flat, in
> `4-SYSTEM/scripts/payloads/`, POSTed by `upload_liturgy.py` to `/v2/texts`
> and `/v2/texts/{text_id}/editions`. Re-trace before relying on this section.

> **Updating texts that are already uploaded (2026-09-15):** the experts edit
> `0-INBOX/`; the uploaded state is `1-SOURCES/Text/`. Run
> `python3 4-SYSTEM/scripts/inbox_diff.py --report 4-SYSTEM/inbox-diff-report.md`
> (skill `inbox-diff`) to see what changed and what it costs to push, and read
> `4-SYSTEM/backend-update-plan.md` for which v2 endpoints exist for an
> in-place update and which are missing. Never delete and re-create a text:
> its `text_id` is referenced everywhere.

`build_payloads.py` emits, per note, `text.json` (`POST /v2/texts`),
`instance.json` (`POST /v2/texts/{text_id}/instances` — content plus span
annotations) and `toc.json` (`POST /v2/annotations/{instance_id}/annotation`,
`type: table_of_contents`). It asserts every span slices back to exactly
its source block. TOC sections reference segment *indices*; the uploader
substitutes the backend-assigned segment ids returned by the instance POST.

## 4. Working rules

- Read the folder's `About <Folder>.md` before writing into it.
- `1-SOURCES/` holds no interpretation. Interpretive claims go to `2-RAILS/`.
- Citation flows one way: `1-SOURCES/ → 2-RAILS/ → 3-TRANSFORMATIONS/`.
  Nothing downstream cites `0-INBOX/`.
- Regenerating `1-SOURCES/` from the scripts is safe and reproducible;
  hand-edits to notes are not preserved across a regeneration, so fix the
  formatter rather than the output when a formatting rule is wrong.
- The 12 `layout: prose` texts (see `liturgy-catalog.json`) have the
  least certain segment boundaries — treat their segmentation as
  provisional until a human has reviewed it.

## Tags (backend)

`4-SYSTEM/tag-registry.json` is the source of truth for WeBuddhist tags
(key `facet/value`, localized title, description, backend id).
`stamp_metadata.py --tags` mirrors live `tag_ids` into note frontmatter as `tags:` / `tag_ids:`. `4-SYSTEM/scripts/tags.py sync` creates missing tags idempotently;
`tags.py apply KEY --category CID` attaches a tag to every text in a
category (diff-based, ledger in `4-SYSTEM/tag-ledgers/`); `tags.py verify`
compares the tag listing with the category listing. Tagging a root text
cascades to its translations on the backend. New uploads are not tagged
automatically: re-run `apply practice/chant` after each upload.
