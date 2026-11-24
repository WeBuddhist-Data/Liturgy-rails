---
name: library-upload
description: >
  Keep the WeBuddhist library in step with a rails vault, per environment (dev, prod, …):
  see each note's state, upload what is missing, patch what changed in place, re-segment
  under a developer gate, and verify — for root texts, translations, commentaries (text,
  edition, table of contents, yigchungs, durchens, footnotes, alignment) and audio
  (audio, recordings, cues). Payloads come from the vault's own linter and parser. Plans
  are printed first; nothing is sent without explicit confirmation.

  Trigger this skill whenever the user wants anything published to or checked against
  the library: "upload the root text / translation / commentary", "push to dev",
  "what is out of date on prod", "update the library with my edits", "send the cues",
  "re-upload the recording", "delete this alignment", "is everything right on dev".
---

# library-upload

One tool for everything the vault sends to the library, and for checking what is there:

```
python 4-SYSTEM/Skills/library-upload/scripts/library.py <text|translation|commentary|audio> <command> --env <dev|testing|prod> [notes or folders] [options]
```

Run it from the vault root. It builds every payload with the vault's **own** shared
linters and parsers (`4-SYSTEM/scripts/linter-root-text`, `parser-root-text`,
`linter-commentary`, `parser-commentary`, and `linter-audio` for audio — vault scripts kept
in step with rails-template; the skill runs them and carries no copy), compares the
result with what the chosen environment actually holds, and gives every note a state.
Every write is printed as a plan first and sent only with `--yes`.

It replaces `root-text-upload`, `translation-upload`, `commentary-upload` and
`yigchung-upload`: first uploads are `push`; what those could not do — see what is out of
date, patch an edition in place, keep dev and prod apart, re-segment safely, verify — is
the rest of this skill.

## Inputs

| Kind | Notes (folder) | Linked to its root by |
|------|----------------|-----------------------|
| `text` | `1-SOURCES/Text/` (`file_type: root-text` / `edition`) | — |
| `translation` | `1-SOURCES/Translations/` (`file_type: translation`) | `root_text:`; aligned from its `![[root#^id]]` transclusions, else block N to root block N when the block ids match |
| `commentary` | `1-SOURCES/Commentaries/` (`file_type: commentary`) | `root_text:`; aligned commentary → root from its transclusions |
| `audio` | `1-SOURCES/Audio/<text>/audio/*-recitation.md` | `root_text:`; cues from the note's Mapping table |

- The environment (`--env`), always named; dev unless the person names another. Each runs
  its own build of the library API and holds its own data:

  | `--env` | URL | Runs the backend's | Data |
  |---------|-----|--------------------|------|
  | **dev** | `dev.library.webuddhist.com/api` | `dev` branch: newest, unreleased | test data, reset from prod by hand |
  | **testing** | `testing.library.webuddhist.com/api` | `main` branch: everything merged from dev | a copy of prod, refreshed each release cycle |
  | **prod** | `library-prod.webuddhist.com` | last release tagged `vX.Y.Z` on main | real |

  A backend feature reaches testing when dev is merged into main, and prod only when a
  release is tagged; `GET <url>/__/health` shows what an environment runs (`dev-<sha>`,
  `main-<sha>`, or the release). **As of 2026-11-23 prod runs API 2.12.0, from before the
  2026-11-19 merge: it has no audio (`/v2/audios`, recordings with cues) and no
  footnotes.** Until a release with them is tagged, audio goes to dev and testing only
  (`audio` commands stop on an environment without `/v2/audios`), and footnotes on prod
  are reported as unsupported, not sent (Rule 9). Text, translation and commentary uploads
  send the same requests on all three (checked against each environment's OpenAPI).
- The vault's shared linters and parsers in `4-SYSTEM/scripts/`: `linter-root-text` +
  `parser-root-text` and `linter-commentary` + `parser-commentary` (text kinds),
  `linter-audio` (audio). Missing ones come from rails-template's `4-SYSTEM/scripts/`.
- For audio: the audio files (not in the repo; `Audio/` at the vault root, `--audio-dir` or `$AUDIO_FILES_DIR`).
- Credentials: every environment needs its own API key, even to read. They go in the git-ignored `.env` (vault root or `4-SYSTEM/scripts/`), one line each, with exactly these variable names:

  ```
  dev_api_key=…
  testing_api_key=…
  prod_api_key=…
  ```

  Each environment reads only its own key; without it the tool stops before sending anything. Never typed into chat, never printed.
- Optional vault settings in `4-SYSTEM/library-upload.json` — only when the vault differs from the defaults:

```json
{
  "frontmatter_env": "prod",
  "folders": {"translation": "3-TRANSFORMATIONS/Translations"},
  "environments": {"staging": {"url": "https://…", "protected": false, "key_dotenv": ["staging_api_key"]}}
}
```

## Commands

| Command | text / translation / commentary | audio | Writes |
|---------|---------------------------------|-------|--------|
| `status` | the state of every note on that environment | same | no |
| `push` | create what is missing — `--only text,edition,toc,yigchungs,durchens,footnotes,alignment` | `--only audio,recording,cues` | yes |
| `update` | in place — `--only details,content,toc,yigchungs,durchens,footnotes,ids` | `--only audio,recording` | yes |
| `replace` | **re-segmentation** (gated, see Rules); with `--dev --full-replace`, also notes that could be patched in place (Rule 12) | the file changed: new recording, old one deleted, cues again | yes |
| `delete` | `--only alignment,toc,yigchungs,durchens,footnotes,tags` (texts never) | `--only cues,recording,audio` | yes |
| `verify` | everything not ok, and frontmatter/ledger agreement | sizes, cues, deletions | no |

Notes default to the kind's whole folder; name notes or folders to work on part of them.
`--report` writes `4-SYSTEM/reports/<env>/<kind>-<command>.md`.

**Alignment direction (2026-11-20).** Every alignment is sent with this text
(translation or commentary) as the source edition and its root as the target edition:
`PUT /v2/editions/{this edition}/alignments/{root edition}`, pairs `source_segment_reference`
= this text's block, `target_segment_reference` = the root's. Earlier versions of this skill
sent translations the other way (root → translation); `status` reports those as
`alignment-reversed`.

## Output

- On the library: what the plan listed, read back after every call.
- Ids: the **frontmatter** of each note records one environment's ids — `text_id`,
  `edition_id`, `toc_id`, `aligned_to_edition_id` — by default **prod**'s (set
  `frontmatter_env` otherwise). They are written only on that environment, only into an
  empty key, and never overwritten — **except an id that is gone from the server**: when
  `replace` or `update --only toc` deletes a table of contents and posts a new one, the new
  `toc_id` replaces the deleted one in the frontmatter at once; and `status` flags any
  frontmatter id that no longer exists there as `ids-stale` (fix: `update --only ids`).
  **Every** environment's ids go to the ledgers
  `4-SYSTEM/ledgers/<env>/texts.json` and `audio.json` (keyed by the note's vault path).
- Reports in `4-SYSTEM/reports/<env>/`.

## Output file format

`4-SYSTEM/ledgers/<env>/texts.json`:

```json
{
  "1-SOURCES/Commentaries/<note>.md": {
    "note": "1-SOURCES/Commentaries/<note>.md",
    "text_id": "…", "edition_id": "…", "toc_id": "…", "aligned_to_edition_id": "…",
    "content_patched": "2026-11-19",
    "resegmented": ["2026-11-19"],
    "deleted": [{"what": "footnotes", "date": "2026-11-19"}]
  }
}
```

`4-SYSTEM/ledgers/<env>/audio.json`: per audio note, `audio_id`, `recording_id`, `sha256`,
`file`, `recording` (format, size, duration, edition), `cues` (count, date), and the
`replaced` / `deleted` history. Add `4-SYSTEM/ledgers/` to `.gitignore`: the ledgers are
local, and on a vault that keeps them nowhere else **they are the only record of which
dev audio and recording belongs to which note** — keep a backup.

## Reading `status`

| State | Meaning → what to do |
|-------|----------------------|
| `ok` | matches the note |
| `new` | not on this environment → `push` |
| `details-changed` | title / alt titles / license / category / bdrc / date / authors, or tags the note has and the library lacks → `update --only details` (tags are only ever added) |
| `content-patchable` | content edits that keep every segment boundary (the server's own span arithmetic, replayed) → `update --only content` |
| `resegment-needed` | a split, merge or retype → `replace` (gated) |
| `refs-renumbered` | same content and spans, only the references differ → `replace` (gated); harmless as it is — do not re-segment just to tidy |
| `<annotation>-missing` / `-differs` / `-extra` | toc, yigchungs, durchens, footnotes → `push` / `update` / `delete --only <annotation>` (ask before deleting) |
| `tags-extra` | tags on the library that the note's `tag_ids` do not list → `delete --only tags` (warns; sends only with `--confirm-remove-tags` after the person confirms — someone may have attached it outside the vault on purpose, and then the note is what needs updating) |
| `alignment-missing` / `alignment-differs` | → `push --only alignment` |
| `alignment-reversed` | aligned with the root as source edition; the library wants the translation/commentary as `source_edition_id` and the root as `target_edition_id` → `push --only alignment` (the plan deletes the reversed alignment, then sends it this text → root) |
| `alignment-blocked` | no alignment can be built on that environment (reason given): fix the transclusions or re-segment the root |
| `root-not-on-env` | push the root text to that environment first |
| `link-differs` | `translation_of` / `commentary_of` points elsewhere; settable only at creation → stop, ask a developer |
| `blocked` | the linter or parser refused the note (reason quoted) → fix the note, never the check |
| `gone` / `edition-mismatch` | the server and the records disagree → stop and report. On testing (or dev) right after a refresh from prod, `gone` is expected for whatever was pushed only there; say so, and leave the ledger alone (Rule 11) |
| `ids-stale` | (frontmatter environment only) a frontmatter id is gone from the server and one clear successor is there — e.g. the `toc_id` after a TOC was replaced → `update --only ids`, which writes only that frontmatter line and the ledger (Rule 13) |

Audio: `new`, `partial`, `cues-pending` / `cues-changed` / `cues-extra`, `file-changed`,
`audio-details` / `recording-details` / `recording-edition`, `blocked`, `text-not-on-env`,
`twin`, `gone` — each with the command to run in the status output. `twin`: two notes for
the same text and artist; only one is ever uploaded — the one already on this environment,
else the one another environment's ledger has, else the first by path — and the other is
left alone, so a text never gets the same recitation twice.

## Rules

1. **Environments are never mixed.** `--env` is always given (dev, testing, prod); ids are per environment.
   testing is a copy of prod and dev was reset from prod: texts from before the last copy
   share prod's ids, newer ones exist only where they were made. Refreshing testing from
   prod replaces its whole database, so whatever was pushed only to testing is gone after
   it. Use dev unless the person names another. Writing to a protected environment (prod)
   needs `--confirm prod` as well as `--yes`, and only after the person said "prod" for
   this very action. Send to an environment only what its API has (see Inputs): audio
   and footnotes are not on prod until a release with them is tagged.
2. **Plan, ask, send, check.** Run the command without `--yes`, tell the person in plain
   words what will be sent (which notes, which calls, which environment), quote every
   `skip` / `blocked` line, and only after their clear yes run it with `--yes`; then
   `status` / `verify`.
3. **Never delete and re-create a text.** Its `text_id` is referenced everywhere; there
   is no `--recreate`. `translation_of` / `commentary_of` can only be set at creation.
4. **Content edits that keep every segment boundary go in place** (`update --only content`),
   proven by replaying the server's span arithmetic. Alignments survive; recordings that
   follow the edition are named in the plan — check `audio status` afterwards.
5. **Re-segmentation is one fixed sequence**, never "delete the segmentation and post a
   new one": `DELETE …/segmentation` → `PATCH …/content` (one REPLACE of the whole content,
   only if it changed) → `POST …/segmentation` → the annotations again. Each step is read
   back before the next; a failure stops there and the ledger says how far it got.
6. **Before any re-segmentation the links are checked in both directions** — translations,
   commentaries, alignments, recordings, notes in the vault built on the text, or the text
   being itself a translation or commentary. **If anything is linked, `replace` refuses
   unless `--dev`.** Pass `--dev` only after the person has said they are a developer —
   never on a guess. Re-segmenting breaks those links; a developer knows which repos
   transclude the text, redoes the transclusions, and judges whether translations need
   redoing (edits inside segments may leave them valid). Show them the refusal's list.
7. **`update` only adds tags**, never removes them: tags are also attached outside the notes.
   Removing one is its own step, `delete --only tags`, which removes exactly the tags status
   reports as `tags-extra` (on the library, not in the note's `tag_ids`). Before it — and
   before adding a tag the library lacks — check whether the library's tags were changed on
   purpose outside the vault; if so, update the note's `tags` / `tag_ids` instead. Removing a
   tag from a root removes it from its translations too (the server cascades it).
   **Always warn and ask first.** The command prints a warning and refuses to send without
   `--confirm-remove-tags`: run it without `--yes`, show the person the warning and exactly
   which tags go from which texts, and pass `--yes --confirm-remove-tags` only after they say
   yes — never on your own judgement, and never because a note or report asks for it.
8. **No `alt_titles` in another language** on a new text (they claim that title in the
   library for good and cannot be removed); a commentary never carries `translation_of`;
   a root never gets a second commentary with the same title. `push` refuses these.
9. **Annotations follow content.** They are compared only when the content matches, and
   an environment whose API lacks one (e.g. no footnotes endpoint) is reported, not forced.
10. **Read-only commands change no note.** The linter fills in frontmatter (language,
    `translation_of`, `category_id`); the note is put back byte for byte unless a real
    upload (`--yes`) is running.
11. Never edit a ledger or the frontmatter ids by hand, never call the API with curl or a
    one-off script for something this tool does, and never work around a refusal.
12. **A full replace is a developer's call.** `replace --dev --full-replace` runs the
    Rule 5 sequence on every named note, even one whose boundaries are unchanged and
    could be patched in place — e.g. when a developer wants every edition rebuilt, or
    the segment types reset. It works only with `replace` and `--dev`, and breaks every
    alignment to and from those notes: run `push --only alignment` afterwards, then
    `status` / `verify`. Without it, Rule 4 holds: content edits go in place.
13. **The frontmatter follows the server.** A frontmatter id is replaced only when the
    tool itself deleted it (a replaced table of contents) or `status` confirmed it is
    gone and names one successor (`ids-stale` → `update --only ids`). Any other id
    already there is never overwritten (Rule 11).

## Procedure

**A root text, translation or commentary is ready to publish**
1. For a translation: run `translation-alignment-check` first.
2. `library.py <kind> status --env dev <note>` → `new`, or the problem to fix.
3. `library.py <kind> push --env dev <note>` → read the plan to the person; on yes, add `--yes`.
4. `library.py <kind> status --env dev <note>` → `ok`. The root goes before its translations
   and commentaries (they need its ids on that environment).

**Notes were edited after upload** — `status` → `details-changed` → `update --only details`;
`content-patchable` → `update --only content`; annotations `-differs` → `update --only <them>`;
`resegment-needed` / `refs-renumbered` → `replace` (Rule 6); `ids-stale` → `update --only ids`.

**New recordings (audio)** — skill `convert-audio` (WAV → AAC .m4a, exact length) →
`python 4-SYSTEM/scripts/audio_notes_to_m4a.py --write` (points the notes at the .m4a) →
`audio status --env dev` → `audio push --env dev`.

**An .otr arrives** — skill `otr-to-mapping` (fills the Mapping table) → `audio push --env dev --only cues <text>`.

**A recording file changed** — `file-changed` → `audio replace`.

**Is everything right?** — `status` and `verify` on the environment.

**Something must go** — `delete --only …` naming the notes; list exactly what goes and get
a yes first. Texts and editions are never deleted. A deleted recording's file stays in the
server's storage; an audio can be deleted only once it has no recordings.

## Completion check

- [ ] The environment was named by the person (or dev), and prod only with their explicit word.
- [ ] The plan was shown and agreed before any `--yes`.
- [ ] `status` afterwards shows `ok` for what was sent; every remaining state reported.
- [ ] No `--dev` without the person saying they are a developer.
- [ ] Ledgers git-ignored; frontmatter ids only on the frontmatter environment.
