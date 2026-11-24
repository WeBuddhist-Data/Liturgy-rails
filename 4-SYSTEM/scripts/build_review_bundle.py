#!/usr/bin/env python3
"""Build 5-REVIEW/ — a reader-facing split of the corpus into what is finished
and what is not, so a reviewer can open a text and see every language at once.

Nothing here is canonical. Every file is a verbatim copy of the note it came
from; the canonical copies stay in 0-INBOX, 1-SOURCES/Text and
3-TRANSFORMATIONS/Translations. Re-run this after any upload or repair:

    python3 4-SYSTEM/scripts/build_review_bundle.py

"Done" means the upload ledger has the Tibetan edition plus all six
translations, each one carrying `aligned: true`. Anything short of that is not
done, and the reason is spelled out in the not-done folder.
"""

import datetime
import json
import os
import re
import shutil
import sys

VAULT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INBOX = os.path.join(VAULT, "0-INBOX")
SRC_DIR = os.path.join(VAULT, "1-SOURCES", "Text")
TRACKS = os.path.join(VAULT, "3-TRANSFORMATIONS", "Translations")
LEDGER = os.path.join(VAULT, "4-SYSTEM", "scripts", "upload_ledger.json")
RETIRED = os.path.join(VAULT, "4-SYSTEM", "retired-texts.txt")
OUT = os.path.join(VAULT, "5-REVIEW")

# lang tag -> (track directory, English name, endonym)
LANGS = [
    ("en", "Dharmamitra/en", "English", "English"),
    ("zh", "Dharmamitra/zh", "Chinese", "中文"),
    ("hi", "Gemini/hi", "Hindi", "हिन्दी"),
    ("ne", "Gemini/ne", "Nepali", "नेपाली"),
    ("mn", "Gemini/mn", "Mongolian", "Монгол"),
    ("vi", "Gemini/vi", "Vietnamese", "Tiếng Việt"),
]


def read_frontmatter(path):
    """Return the frontmatter mapping of a note (flat scalars only)."""
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.readline().rstrip("\n") != "---":
                return out
            for line in fh:
                if line.rstrip("\n") == "---":
                    break
                m = re.match(r"^([A-Za-z_][\w]*):\s*(.*)$", line.rstrip("\n"))
                if m:
                    out[m.group(1)] = m.group(2).strip().strip('"')
    except OSError:
        pass
    return out


def load_retired():
    stems = set()
    if not os.path.exists(RETIRED):
        return stems
    with open(RETIRED, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                stems.add(line)
    return stems


def norm(stem):
    """Inbox names carry decorative trailing shad/space; source names do not."""
    return re.sub(r"[\s།]+$", "", stem)


def classify(stems, ledger):
    """Split source stems into (done, incomplete) using the upload ledger."""
    done, incomplete = [], []
    for stem in stems:
        missing = []
        if stem not in ledger:
            missing.append("bo — never uploaded")
        for tag, _dir, name, _endo in LANGS:
            rec = ledger.get(f"{stem}-{tag}")
            if rec is None:
                missing.append(f"{tag} — never uploaded")
            elif not rec.get("aligned"):
                missing.append(f"{tag} — uploaded but not aligned to the Tibetan")
        (done if not missing else incomplete).append((stem, missing))
    return done, incomplete


def copy_bundle(stem, dest):
    """Copy the Tibetan note and every translation of it into dest/."""
    os.makedirs(dest, exist_ok=True)
    copied = []
    src = os.path.join(SRC_DIR, f"{stem}.md")
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(dest, f"{stem}-bo.md"))
        copied.append("bo")
    for tag, track, _name, _endo in LANGS:
        path = os.path.join(TRACKS, track, f"{stem}-{tag}.md")
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(dest, f"{stem}-{tag}.md"))
            copied.append(tag)
    return copied


def main():
    if not os.path.exists(LEDGER):
        sys.exit(f"no upload ledger at {LEDGER}")
    ledger = json.load(open(LEDGER, encoding="utf-8"))
    retired = load_retired()

    stems = sorted(
        os.path.splitext(n)[0] for n in os.listdir(SRC_DIR) if n.endswith(".md")
    )
    done, incomplete = classify(stems, ledger)

    # Inbox notes that never became source notes and are not deliberately retired.
    source_norm = {norm(s) for s in stems}
    unprocessed = []
    for name in sorted(os.listdir(INBOX)):
        if not name.endswith(".md"):
            continue
        stem = os.path.splitext(name)[0]
        if norm(stem) in source_norm or stem in retired:
            continue
        unprocessed.append(stem)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    for stem, _missing in done:
        copy_bundle(stem, os.path.join(OUT, "done", stem))

    for stem, _missing in incomplete:
        copy_bundle(stem, os.path.join(OUT, "not-done", "1-upload-incomplete", stem))

    raw_dir = os.path.join(OUT, "not-done", "2-never-processed")
    os.makedirs(raw_dir, exist_ok=True)
    for stem in unprocessed:
        shutil.copy2(
            os.path.join(INBOX, f"{stem}.md"), os.path.join(raw_dir, f"{stem}.md")
        )

    write_docs(done, incomplete, unprocessed, retired)

    print(f"5-REVIEW/ rebuilt")
    print(f"  done/                          {len(done)} texts")
    print(f"  not-done/1-upload-incomplete/  {len(incomplete)} texts")
    print(f"  not-done/2-never-processed/    {len(unprocessed)} texts")
    print(f"  retired (not included)         {len(retired)} texts")


def english_alt_title(path):
    """The first `- en: …` entry of the note's `alt_titles` list, or ""."""
    import yaml
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    try:
        alts = (yaml.safe_load(m.group(1)) or {}).get("alt_titles") or [] if m else []
    except yaml.YAMLError:
        return ""
    for e in alts if isinstance(alts, list) else []:
        if isinstance(e, dict) and e.get("en"):
            return str(e["en"])
    return ""


def titles(stem):
    path = os.path.join(SRC_DIR, f"{stem}.md")
    fm = read_frontmatter(path)
    return fm.get("title", stem), english_alt_title(path)


def write_docs(done, incomplete, unprocessed, retired):
    from textwrap import dedent

    total = len(done) + len(incomplete)

    # ---- README ------------------------------------------------------------
    lines = [
        "# Review folder — what is finished and what is not",
        "",
        f"Built {datetime.date.today().isoformat()} by "
        "`4-SYSTEM/scripts/build_review_bundle.py`.",
        "",
        "## In one line",
        "",
        f"Of the {total} liturgy texts in the collection, **{len(done)} are "
        f"finished** and live on the website in all seven languages. "
        f"**{len(incomplete)}** got stuck part-way through publishing, and "
        f"**{len(unprocessed)}** more are still raw text that has not been "
        "started yet.",
        "",
        "## The two folders",
        "",
        "```",
        "5-REVIEW/",
        f"  done/                          {len(done)} texts — finished, checked, published",
        f"  not-done/",
        f"    1-upload-incomplete/         {len(incomplete)} text — translated, but publishing failed",
        f"    2-never-processed/           {len(unprocessed)} texts — raw, not started",
        "```",
        "",
        "### `done/`",
        "",
        "One folder per text. Open a folder and you get the same liturgy in "
        "seven files, one per language:",
        "",
        "| File ends in | Language |",
        "| --- | --- |",
        "| `-bo.md` | Tibetan — the original |",
    ]
    for tag, _dir, name, endo in LANGS:
        label = name if endo == name else f"{name} ({endo})"
        lines.append(f"| `-{tag}.md` | {label} |")
    lines += [
        "",
        "Every line of every translation is numbered to match the Tibetan, so "
        "you can read them side by side: the block marker `^7` at the end of a "
        "Tibetan verse marks the same verse as `^7` in the English, the "
        "Chinese, and all the rest.",
        "",
        "For a text to sit in `done/` it had to clear all of this:",
        "",
        "1. The Tibetan is segmented and numbered.",
        "2. All six translations exist and line up with the Tibetan, verse for verse.",
        "3. The Tibetan and all six translations are uploaded to the website.",
        "4. The website has recorded the link between each translation and the "
        "Tibetan verse it belongs to (the *alignment*), so a reader can tap a "
        "Tibetan line and see it in their own language.",
        "",
        "See [`done/index.md`](done/index.md) for the full list with titles.",
        "",
        "### `not-done/`",
        "",
        "Two groups, because they need two different kinds of work.",
        "",
        f"**`1-upload-incomplete/` — {len(incomplete)} text.** The translation "
        "work is finished; the publishing step is not. The folder has the same "
        "seven files as any `done/` text, so it reads normally — it is only "
        "the website side that is missing. What exactly is missing is written "
        "in that folder's own `WHAT-IS-MISSING.md`.",
        "",
        f"**`2-never-processed/` — {len(unprocessed)} texts.** Raw liturgies "
        "that came in but have not been started. They are not numbered, not "
        "translated, and not on the website. They are here so nothing is "
        "quietly forgotten. See that folder's `WHAT-IS-MISSING.md`.",
        "",
        "## About the translations",
        "",
        "**These are machine translations and have not been checked by a "
        "human.** English and Chinese come from DharmaMitra; Hindi, Nepali, "
        "Mongolian and Vietnamese come from Google Gemini. They are a first "
        "draft — a starting point for a translator or a reviewer, not a "
        "finished translation. The Tibetan is the authority; where a "
        "translation and the Tibetan disagree, the Tibetan is right.",
        "",
        "## Please note",
        "",
        "The files here are **copies**, made so this folder can be read and "
        "passed around on its own. Do not edit them — corrections will be lost "
        "the next time this folder is rebuilt. The originals live in:",
        "",
        "- `1-SOURCES/Text/` — the Tibetan",
        "- `3-TRANSFORMATIONS/Translations/` — the translations",
        "- `0-INBOX/` — the raw incoming text",
        "",
        "To rebuild after an upload or a repair:",
        "",
        "```bash",
        "python3 4-SYSTEM/scripts/build_review_bundle.py",
        "```",
    ]
    if retired:
        lines += [
            "",
            "## Not counted here",
            "",
            f"{len(retired)} text is *retired* — a duplicate of another text "
            "that was entered twice. It stays in `0-INBOX` as a record but is "
            "deliberately excluded from the collection, so it appears in "
            "neither folder. See `4-SYSTEM/retired-texts.txt`.",
        ]
    open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write(
        "\n".join(lines) + "\n"
    )

    # ---- done/index.md -----------------------------------------------------
    idx = [
        f"# The {len(done)} finished texts",
        "",
        "All seven languages present, uploaded, and aligned. Click a title to "
        "open its folder.",
        "",
        "| # | Folder | Title (English) |",
        "| --- | --- | --- |",
    ]
    for i, (stem, _m) in enumerate(done, 1):
        _bo, en = titles(stem)
        idx.append(f"| {i} | [{stem}](<{stem}/>) | {en} |")
    open(os.path.join(OUT, "done", "index.md"), "w", encoding="utf-8").write(
        "\n".join(idx) + "\n"
    )

    # ---- not-done/1-upload-incomplete/WHAT-IS-MISSING.md -------------------
    d1 = os.path.join(OUT, "not-done", "1-upload-incomplete")
    os.makedirs(d1, exist_ok=True)
    m = [
        "# Translated, but not fully published",
        "",
        "These texts are finished as documents — the Tibetan is numbered and "
        "all six translations are there, so the folders read exactly like the "
        "ones in `done/`. What is missing is on the website side.",
        "",
    ]
    for stem, missing in incomplete:
        bo, en = titles(stem)
        m += [f"## {bo}" + (f" — {en}" if en else ""), "", f"Folder: `{stem}/`", ""]
        m += ["Missing:", ""]
        for x in missing:
            m.append(f"- {x}")
        m.append("")
    open(os.path.join(d1, "WHAT-IS-MISSING.md"), "w", encoding="utf-8").write(
        "\n".join(m) + "\n"
    )

    # ---- not-done/2-never-processed/WHAT-IS-MISSING.md ---------------------
    d2 = os.path.join(OUT, "not-done", "2-never-processed")
    m = [
        "# Not started",
        "",
        "Raw liturgy texts that arrived in `0-INBOX` but have never been put "
        "through the pipeline. For each one, everything is still to do:",
        "",
        "1. Segment the Tibetan into verses and number them.",
        "2. Create the source note in `1-SOURCES/Text/`.",
        "3. Translate into the six languages.",
        "4. Upload and align on the website.",
        "",
        "The files below are copies of the raw text, exactly as it came in.",
        "",
        "| Text | Lines |",
        "| --- | --- |",
    ]
    for stem in unprocessed:
        path = os.path.join(INBOX, f"{stem}.md")
        n = sum(1 for _ in open(path, encoding="utf-8"))
        m.append(f"| `{stem}.md` | {n} |")
    open(os.path.join(d2, "WHAT-IS-MISSING.md"), "w", encoding="utf-8").write(
        "\n".join(m) + "\n"
    )


if __name__ == "__main__":
    main()
