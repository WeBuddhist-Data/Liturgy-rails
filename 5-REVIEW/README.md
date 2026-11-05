# Review folder — what is finished and what is not

Built 2026-09-21 by `4-SYSTEM/scripts/build_review_bundle.py`.

## In one line

Of the 113 liturgy texts in the collection, **92 are finished** and live on the website in all seven languages. **21** got stuck part-way through publishing, and **1** more are still raw text that has not been started yet.

## The two folders

```
5-REVIEW/
  done/                          92 texts — finished, checked, published
  not-done/
    1-upload-incomplete/         21 text — translated, but publishing failed
    2-never-processed/           1 texts — raw, not started
```

### `done/`

One folder per text. Open a folder and you get the same liturgy in seven files, one per language:

| File ends in | Language |
| --- | --- |
| `-bo.md` | Tibetan — the original |
| `-en.md` | English |
| `-zh.md` | Chinese (中文) |
| `-hi.md` | Hindi (हिन्दी) |
| `-ne.md` | Nepali (नेपाली) |
| `-mn.md` | Mongolian (Монгол) |
| `-vi.md` | Vietnamese (Tiếng Việt) |

Every line of every translation is numbered to match the Tibetan, so you can read them side by side: the block marker `^7` at the end of a Tibetan verse marks the same verse as `^7` in the English, the Chinese, and all the rest.

For a text to sit in `done/` it had to clear all of this:

1. The Tibetan is segmented and numbered.
2. All six translations exist and line up with the Tibetan, verse for verse.
3. The Tibetan and all six translations are uploaded to the website.
4. The website has recorded the link between each translation and the Tibetan verse it belongs to (the *alignment*), so a reader can tap a Tibetan line and see it in their own language.

See [`done/index.md`](done/index.md) for the full list with titles.

### `not-done/`

Two groups, because they need two different kinds of work.

**`1-upload-incomplete/` — 21 text.** The translation work is finished; the publishing step is not. The folder has the same seven files as any `done/` text, so it reads normally — it is only the website side that is missing. What exactly is missing is written in that folder's own `WHAT-IS-MISSING.md`.

**`2-never-processed/` — 1 texts.** Raw liturgies that came in but have not been started. They are not numbered, not translated, and not on the website. They are here so nothing is quietly forgotten. See that folder's `WHAT-IS-MISSING.md`.

## About the translations

**These are machine translations and have not been checked by a human.** English and Chinese come from DharmaMitra; Hindi, Nepali, Mongolian and Vietnamese come from Google Gemini. They are a first draft — a starting point for a translator or a reviewer, not a finished translation. The Tibetan is the authority; where a translation and the Tibetan disagree, the Tibetan is right.

## Please note

The files here are **copies**, made so this folder can be read and passed around on its own. Do not edit them — corrections will be lost the next time this folder is rebuilt. The originals live in:

- `1-SOURCES/Text/` — the Tibetan
- `3-TRANSFORMATIONS/Translations/` — the translations
- `0-INBOX/` — the raw incoming text

To rebuild after an upload or a repair:

```bash
python3 4-SYSTEM/scripts/build_review_bundle.py
```

## Not counted here

2 text is *retired* — a duplicate of another text that was entered twice. It stays in `0-INBOX` as a record but is deliberately excluded from the collection, so it appears in neither folder. See `4-SYSTEM/retired-texts.txt`.
