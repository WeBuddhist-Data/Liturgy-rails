---
name: Library-Upload
description: Upload to the WeBuddhist library and keep it in step with the vault — root texts, translations and commentaries (text, edition, alignment) and audio (audio, recordings, cues). Use whenever asked to upload, push, sync, update, re-upload, replace, re-segment or delete anything on the library (dev or prod), to send cues or alignments, to check what is uploaded or out of date, or when new recordings, .otr files, translations, commentaries or edited notes arrive.
---

# Library-Upload

One tool, run from the vault root:

```
python 4-SYSTEM/scripts/library.py <text|translation|commentary|audio> <command> --env <dev|prod> [notes or folders] [options]
```

| Kind | Notes (default folder) | Linked to its root by |
|------|------------------------|-----------------------|
| `text` | root texts, `1-SOURCES/Text` | — |
| `translation` | `3-TRANSFORMATIONS/Translations/<track>/<lang>/<root>-<lang>.md` | `translation_of`; aligned root → translation, block N with root block N |
| `commentary` | `1-SOURCES/Commentary/` (any path can be named) | `commentary_of`; aligned commentary → root from its `![[…#^id]]` transclusions |
| `audio` | `1-SOURCES/Audio` | `root_text` |

| Command | text / translation / commentary | audio | Writes? |
|---------|--------------------|-------|---------|
| `status` | state of every note on that environment | same | no |
| `push` | creates what is missing: `--only text,edition` (+ `alignment` for a translation or commentary) | `--only audio,recording,cues` | yes |
| `update` | PATCH in place: `--only details,content` (content only when every segment boundary is kept) | `--only audio,recording` | yes |
| `replace` | **re-segmentation** (gated, see the rules) | file changed: new recording, old deleted, cues re-sent | yes |
| `delete` | translation / commentary `--only alignment`; **texts are never deleted** | `--only cues\|recording\|audio` | yes |
| `verify` | everything not ok, and note/ledger id agreement | sizes, cues, deletions | no |

Every write command prints its plan and **sends nothing without `--yes`**.
`--report` writes `4-SYSTEM/reports/<env>/<kind>-<command>.md`. Notes default to
the kind's folder; name notes or folders to work on part of them.

## Environments — never mix them

- **dev** (`library.webuddhist.com`) and **prod** (`library-prod.webuddhist.com`)
  are separate databases; a `test` environment will follow. dev was replicated
  from prod once: older texts share ids, newer texts exist only on prod, and
  **audio exists only on dev** so far.
- `--env` is always required; ids and ledgers are per environment
  (`4-SYSTEM/ledgers/<env>/audio.json`, `texts.json`). Never copy an id from one
  environment to another. (2026-11-19: all 665 texts of this vault exist on both
  with the same ids; dev and prod content has already drifted for a few.)
- Use **dev** unless the person names prod. Writing to prod needs
  `--confirm prod` as well as `--yes`; only add it after the person has said
  "prod" for this very action.

## The way every job goes

1. `status` (or the command without `--yes`) — read the plan.
2. Tell the person, in plain words, what will be sent: which notes, which calls,
   on which environment. Quote every `blocked` / `skip` line.
3. Only after they agree: the same command with `--yes`.
4. `verify` (and `status`) afterwards; report what changed and what did not.

Never edit a ledger by hand, never call the API with curl or a one-off script
for something the tool does, and never work around a refusal.

## Audio flows

| Situation | Run |
|-----------|-----|
| New recordings (WAV) arrive | skill **Convert-Audio** → `python 4-SYSTEM/scripts/audio_notes_to_m4a.py --write` → `status` → `push` |
| An `.otr` arrives | skill **OTR-To-Mapping** (fills the Mapping table) → `push --only cues <that text>` |
| A note's title / license / narrator changed | `status` shows `audio-details` → `update --only audio` |
| Recording title or duration changed | `recording-details` → `update --only recording` |
| The root text's edition changed | `recording-edition` → `update --only recording` (re-sends the cues: changing edition drops them) |
| A better take / re-encoded file | `file-changed` → `replace` |
| Something must go | `delete --only …` naming the notes — list exactly what goes and get a yes first. A deleted recording's file stays in the server's storage. An audio can be deleted only once it has no recordings. |
| "Is everything right on dev?" | `status` + `verify` |

## Reading `status`

| State | Meaning → what to do |
|-------|----------------------|
| `ok` | matches the note |
| `new` | not on this environment → `push` |
| `partial` | audio without recording → `push --only recording` |
| `cues-pending` / `cues-changed` | → `push --only cues` |
| `cues-extra` | cues on the server, none in the note → ask; `delete --only cues` |
| `file-changed` | → `replace` |
| `audio-details` / `recording-details` / `recording-edition` | → `update` |
| `blocked` | lint errors (quoted): fix the note, never the check. Common: missing `license`, artist without `[op:ID]`, broken `root_text` link, file size/sha256 not matching |
| `text-not-on-env` | the root text is not on this environment (e.g. prod-only text, audio on dev) — the text must be uploaded there first |
| `twin` | another note for the same text and artist is the uploaded one (same take filed twice) — nothing to do |
| `gone` / `extra-recordings` | the server and the ledger disagree — stop and report; do not "fix" by deleting |

## Text and translation flows

| Situation | Run |
|-----------|-----|
| Expert edits reached `1-SOURCES/Text` | `text status` → `content-patchable` → `text update --only content` (then `audio status` if it warns about recordings) |
| A text's title / license / category / authors changed | `details-changed` → `text update --only details` |
| A new root text (block ids stamped, skill Add-Block-ID; `category_id` in the note) | `text status` (`new`) → `text push` → tags: `tags.py apply …` |
| A new translation | its root must be on the environment → `translation push` (text, edition, alignment) |
| Boundaries moved / ids renumbered | `resegment-needed` / `refs-renumbered` → `text replace` — **developer only if linked** (rules below) |
| A new commentary | its root must be on the environment → `commentary status` → `commentary push` (text with `commentary_of`, edition, alignment) |
| A commentary's transclusions changed | `alignment-differs` → `commentary push --only alignment` |
| After a developer re-segmented a root | `translation status` / `commentary status` → `alignment-differs` → `push --only alignment` |
| An alignment must go | `translation delete --only alignment <note>` (or `commentary …`) |

## Commentary notes

Same shape as a root text note, plus the link to the root — like `translation_of`:

```yaml
---
title: <title, in the note's language>
file_type: commentary
commentary_of: "[[1-SOURCES/Text/<root note>]]"   # a [[link]] or a path; set once, at creation
lang_tag: bo
author: <Name> [person:<id>] [role:author]          # as on root texts; untagged authors are left out
license: <license>
source: <where the text comes from>                  # optional; category_id defaults to the root's
---
```

- Every comment block has a block id (`^1-1`, skill Add-Block-ID).
- Each comment says which root block(s) it explains with transclusions
  `![[1-SOURCES/Text/<root>#^<root block id>]]` — inside the comment block, or on
  their own just above it (the transclusion skills write them). Several per
  comment, and the same root block under several comments, are fine: every
  transclusion becomes one alignment pair (comment block → root block).
- The transclusions are **not** uploaded as text. Transclusions of other notes
  are ignored for the alignment (status notes them).
- Blocked, with the reason: no `commentary_of`, a transclusion of a block the
  root note does not have, a transclusion after the last comment, or no
  transclusion of the root at all.
- The root's block ids are matched to the references that exist on the chosen
  environment by position (the vault renumbered the roots' ids after upload).

## Reading `text` / `translation` status

| State | Meaning → what to do |
|-------|----------------------|
| `ok` | text, content, segmentation (and alignment) match the note |
| `new` | not on this environment → `push` |
| `details-changed` | → `update --only details` |
| `content-patchable` | content edits that keep every segment boundary (proven by replaying the server's own span arithmetic) → `update --only content` |
| `resegment-needed` | a split/merge/retype: only re-segmentation can do it → `replace` (gated) |
| `refs-renumbered` | content and spans identical, only the references differ: the vault renumbered block ids (`0-1, 0-2…`) after upload; the server still has `1, 2…`. Harmless as it is — alignments use the server's references. Changing it is a re-segmentation (gated); do not do it just to tidy up |
| `alignment-missing` / `alignment-differs` | → `push --only alignment` |
| `alignment-blocked` | the root has a different number of segments on the environment than in its note (or translation and root differ in block count): fix the notes or re-segment the root first |
| `root-not-on-env` | push the root text to that environment first |
| `link-differs` | `translation_of` / `commentary_of` points elsewhere; it can only be set at creation: stop, ask a developer |
| `blocked` / `gone` / `edition-mismatch` | stop and report, quoting the reason |

Translations pair block N with root block N **by position**, using the root's
references as they exist on that environment.

## Rules for texts (enforced by the tool)

1. **Never delete and re-create a text.** Its `text_id` is referenced everywhere.
   `translation_of` / `commentary_of` can only be set when the text is created.
2. **Content changes that keep every segment boundary** go in place
   (`update --only content`). Alignments survive. Recordings that follow the
   edition are named in the plan: check `audio status` afterwards.
3. **Re-segmentation is one fixed sequence**, never "delete the segmentation and
   post a new one": `DELETE …/segmentation` → `PATCH …/content` (one REPLACE of
   the whole content, only if it changed) → `POST …/segmentation`. Each step is
   read back before the next; a failure stops there and the ledger says how far
   it got.
4. **Before any re-segmentation the tool checks links in both directions:**
   translations, commentaries, alignments, recordings (with or without cues),
   translation notes in the vault, or the text being itself a translation or
   commentary. **If anything is linked, `replace` refuses unless `--dev`.**
   Pass `--dev` only after the person has said they are a developer — never on a
   guess. Re-segmenting breaks those links; a developer knows which repos
   transclude the text, must redo the transclusions, and judges whether the
   translations need redoing (edits that stayed inside segments may leave them
   valid). Show them the list of links from the refusal.

## Related

- `4-SYSTEM/scripts/library.py` and `4-SYSTEM/scripts/webuddhist/` — the tool
  (environments in `webuddhist/envs.py`)
- `4-SYSTEM/scripts/linter-audio/` — the audio linter every push goes through
- skills Convert-Audio, OTR-To-Mapping
