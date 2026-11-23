# Linter — Audio

Checks audio notes (`1-SOURCES/Audio/<text>/audio/<lang>-<artist>-recitation.md`)
against the v2 audio API and builds their upload payloads. `../library.py`
(skill Library-Upload) runs it on every note and refuses to write for a note
with any **error**; **warnings** do not block.

| File | Role |
|------|------|
| `audio_note.py` | Parser: reads a note into an `AudioNote` (metadata, contributors, root text and its block ids, recordings and their files, Mapping table). Judges nothing |
| `lint_audio.py` | Linter: the rules below, the payloads, the report |

Related: `../otr_to_mapping.py` fills the Mapping table from an .otr file
(skill `otr-to-mapping`), `../library.py audio …` uploads.

## How to run

From the vault root:

```bash
python 4-SYSTEM/Skills/library-upload/scripts/linter-audio/lint_audio.py                  # every note in 1-SOURCES/Audio
python 4-SYSTEM/Skills/library-upload/scripts/linter-audio/lint_audio.py "1-SOURCES/Audio/<text>" --live
python 4-SYSTEM/Skills/library-upload/scripts/linter-audio/lint_audio.py -q               # only notes with errors
```

| Option | |
|--------|--|
| `--live` | read the root text's edition on the API (`--host dev`, default, or any environment in `../webuddhist/envs.py`) and place every timed Mapping row in it: this gives the cue spans |
| `--hash` | sha256 every audio file and compare with the note (slow: ~12 GB of WAV) |
| `--audio-dir DIR` | where the audio files are (see below) |
| `--require-file` | a recording whose file is missing is an error, not a warning (upload mode) |
| `--no-output` | do not write `output/` |
| `--report [FILE]` | write a Markdown report of which notes failed and why, then the warnings by kind (default `4-SYSTEM/audio-lint-report.md`). Notes are Obsidian links |

`library.py audio <command> --env <env> --report [FILE]` writes the same report
for a status, upload or check run (default `4-SYSTEM/reports/<env>/audio-<command>.md`),
adding each note's state, the plan or what was sent, and API failures.

Exit code 1 when any note has an error. Output, per note:
`output/<root text> — <note>.lint.json` (no errors) or `….lint.errors.json`,
holding `audio_input`, `recordings` (metadata, file, content type),
`cue_sources`, `cues` (with `--live`), `errors`, `warnings`, `notes`.

## Audio files are not in the repository

They are too large (~12 GB) and are downloaded separately (e.g. from a GitHub
release). The linter looks for a note's file:

1. next to the note,
2. in `<audio dir>/**/<text folder name>/` (or `…/<text folder name>/audio/`),
3. anywhere in `<audio dir>` with the exact byte size the note gives.

`<audio dir>` is `--audio-dir`, else `$AUDIO_FILES_DIR`, else `Audio/` at the
vault root (git-ignored). Without the files every rule still runs except the
file checks; a missing file is a warning (an error with `--require-file`).
`library.py` blocks a push or replace whose file is missing.

## Rules

### Metadata

| Field | Rule |
|-------|------|
| `file_type` | must be `audio` (E) |
| `title` | keyed by `lang_tag`; missing → W, the audio has no title. Key not a known language code → E |
| `title_en` / `alt_titles: [{en: …}]` | English title; missing → W |
| `lang_tag` | known language code (E); missing → W (the API uses the text's language) |
| `license` | **required** (E): the API stores `public` when none is sent, which nobody stated. Use `unknown` if it is not known. Must be one of `cc0 public cc-by cc-by-sa cc-by-nd cc-by-nc cc-by-nc-sa cc-by-nc-nd copyrighted unknown` |

### Contributors

The API requires **at least one** contribution (`contributions`, minItems 1),
so an audio cannot be uploaded without a person.

`artist` may list several, separated by `,` or `;`. Each needs an id:

| Value | Result |
|-------|--------|
| `Name [op:ID]` | person with that API id |
| `Name [bdrc:P123]` | person with that BDRC id |
| `Name` (no id) | **E** — create the person (`POST /v2/persons`) and tag the line |
| `… [role:singer]` | role; else `performance_type` decides (`recitation` → `narrator`) |

Role must be `narrator`, `singer` or `musician` (E).

### Root text

- `root_text` must hold a `[[link]]` to a file that exists (E). A broken link
  is not guessed around: `root_text_title` is not used to find the file
- the root text note needs `text_id` and `edition_id` (E): the audio is
  attached to the text, the recording follows the edition

### Recordings

Two note layouts are read: top-level fields (`audio_file`,
`duration_seconds`, `file_size_bytes`, `sha256`, `format`) or a
`recordings:` list with the same fields per entry (`file` for the path).

- at least one recording (E); ids unique (E)
- `duration_seconds` a positive number (E); missing → W (the server does not
  compute it)
- `file_size_bytes` a positive integer (E); `sha256` 64 hex digits (E),
  missing → W
- file extension mp3 / wav / m4a / ogg / flac (E); `.mp4` is sent as audio/mp4
  and stored as m4a
- file found: its size must equal `file_size_bytes` (E); with `--hash` its
  sha256 must equal `sha256` (E). Several different files match → E. The same
  file at two places in the drop → W

### Mapping table

Section `## Mapping`, a table with `#`, `Section`, `Text…`, `Start`, `End`.

- no Mapping section → W; a missing Section/Start/End column → E
- a timed row's Section must be a block id `^id` (E) that exists in the root
  text (E). Untimed rows may still carry old labels (`(stanza 1)`) — noted
- Start/End as `m:ss.sss` (also `mm:ss.s`, `h:mm:ss`) (E); both or neither (E);
  End after Start (E); End not past the recording + 0.5 s (E)
- timed rows in time order and not overlapping the previous one (E)
- rows in root-text block order (W); a block repeated (W); the Text column
  not starting like the block (W); a cue shorter than 1 s (W)
- with `--live`: every timed block's text must be found in the live edition,
  in order (E) — that is the cue's span. A block that is not one live segment
  of its own (merged/split there) still gets a span (W)
- no timed rows → noted: upload audio + recording now, cues later
