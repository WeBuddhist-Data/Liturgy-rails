"""Parser for audio notes (`1-SOURCES/Audio/<text>/audio/<lang>-<artist>-<performance>.md`).

Reads one note into an `AudioNote`: metadata, contributors, the root text it
recites (with that note's text_id, edition_id and block ids), its recordings
(with the audio file found on disk) and its Mapping table. It judges nothing;
`lint_audio.py` applies the rules.

Two note layouts exist and both are read:

  flat         recording fields at the top level
               (audio_file, duration_seconds, file_size_bytes, sha256, format)
  recordings   a `recordings:` list, one entry per recording
               (file, version, duration_seconds, file_size_bytes, sha256, format)

The English title is `title_en`, else the `en` entry of `alt_titles`.

Audio files are not kept in the repository (too large); they are downloaded
separately (e.g. from a GitHub release) into a folder of their own. That
folder is `--audio-dir`, else the environment variable `AUDIO_FILES_DIR`,
else `Audio/` at the vault root (git-ignored). A file is looked for next to
the note first, then in the audio folder's subfolder named like the note's
text folder, then anywhere in the audio folder by its exact byte size. A file
that is not found is not an error for the parser: `Recording.file` stays None.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webuddhist.vault import FOLDERS, VAULT  # noqa: E402  (vault root and folders: one place)

NOTES_ROOT = FOLDERS["audio"]
FILES_ROOT = Path(os.environ.get("AUDIO_FILES_DIR") or VAULT / "Audio")
TEXT_ROOT = FOLDERS["text"]


def set_files_root(path) -> None:
    """Point the file lookup at another folder (e.g. an unpacked release)."""
    global FILES_ROOT, _FILE_INDEX
    FILES_ROOT, _FILE_INDEX = Path(path).resolve(), None

# extension -> (API AudioFormat, multipart content type)
AUDIO_EXT = {
    ".mp3": ("mp3", "audio/mpeg"),
    ".wav": ("wav", "audio/wav"),
    ".m4a": ("m4a", "audio/mp4"),
    ".mp4": ("m4a", "audio/mp4"),     # audio-only MP4; the server stores it as m4a
    ".ogg": ("ogg", "audio/ogg"),
    ".flac": ("flac", "audio/flac"),
}

TAG_RE = re.compile(r"\[(op|bdrc|role):\s*([^\]]*?)\s*\]")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
BLOCK_ID_RE = re.compile(r"\s\^([\w-]+)\s*$")
TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")
MAPPING_RE = re.compile(r"^##\s+Mapping\s*$", re.M)


@dataclass
class Contributor:
    raw: str
    name: str
    op: str | None = None
    bdrc: str | None = None
    role: str | None = None          # from [role:…]; None = take it from performance_type


@dataclass
class Recording:
    key: str                         # `id`/`version` in the recordings layout, else "recitation"
    declared: str | None             # file path as written in the note
    duration_seconds: object = None  # as written (validated by the linter)
    size_bytes: object = None
    sha256: str | None = None
    format_label: str | None = None
    file: Path | None = None         # the file found on disk
    found_by: str | None = None      # "next to note" | "Audio/ folder" | "size"
    candidates: list = field(default_factory=list)   # several files and no way to choose


@dataclass
class MappingRow:
    line: int                        # 1-based line in the note
    number: str
    section: str
    text: str
    start: str
    end: str
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass
class AudioNote:
    path: Path
    fm: dict
    body: str
    layout: str                      # "flat" | "recordings"
    title: dict
    language: str | None
    license: str | None
    date: object
    performance_type: str | None
    contributors: list
    root_link: str | None
    root_title: str | None
    root_note: Path | None = None
    root_found_by: str | None = None     # "link" | "root_text_title" | "link name"
    text_id: str | None = None
    edition_id: str | None = None
    root_blocks: dict = field(default_factory=dict)  # block id -> {"text", "heading"}
    recordings: list = field(default_factory=list)
    mapping: list | None = None          # None = no Mapping table
    mapping_columns: list = field(default_factory=list)
    dev_ids: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)     # things that could not be read at all

    @property
    def text_folder(self) -> Path:
        return self.path.parent.parent if self.path.parent.name == "audio" else self.path.parent

    @property
    def stem(self) -> str:
        """Ledger / payload key: `<root text> — <note name>`."""
        root = self.root_note.stem if self.root_note else self.text_folder.name
        return f"{root} — {self.path.stem}"


# ----------------------------------------------------------------- reading

def read_note_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


def split_frontmatter(text: str):
    if not text.startswith("---\n"):
        raise ValueError("no YAML frontmatter (the note must start with ---)")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("frontmatter is not closed by ---")
    fm = yaml.safe_load(text[4:end]) or {}
    if not isinstance(fm, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    return fm, text[end + 4:].lstrip("\n")


def parse_contributors(value) -> list:
    out = []
    if not value:
        return out
    for part in re.split(r"\s*[;,]\s*(?![^\[]*\])", str(value)):
        if not part.strip():
            continue
        c = Contributor(raw=part.strip(), name=TAG_RE.sub("", part).strip())
        for kind, val in TAG_RE.findall(part):
            setattr(c, kind, val or None)
        out.append(c)
    return out


def parse_time(s: str):
    """'m:ss.sss' / 'mm:ss.s' / 'h:mm:ss' -> ms, '' -> None, junk -> ValueError."""
    s = s.strip()
    if not s:
        return None
    m = TIME_RE.match(s)
    if not m:
        raise ValueError(s)
    h, mi, se = int(m.group(1) or 0), int(m.group(2)), float(m.group(3))
    if se >= 60 or (m.group(1) and mi >= 60):
        raise ValueError(s)
    return round((h * 3600 + mi * 60 + se) * 1000)


def parse_mapping(body: str, body_offset: int):
    """-> (rows | None, header cells, problems)."""
    m = MAPPING_RE.search(body)
    if not m:
        return None, [], []
    lines = body[m.end():].split("\n")
    first = body[:m.end()].count("\n") + body_offset + 1     # 1-based line of the heading
    rows, header, problems = [], [], []
    in_table = False
    for i, line in enumerate(lines):
        if line.startswith("#"):
            break
        if not line.strip().startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not in_table:
            header, in_table = [c.lower() for c in cells], True
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        col = {name: idx for idx, name in enumerate(header)}

        def cell(*names):
            for n in names:
                for h, idx in col.items():
                    if h.startswith(n) and idx < len(cells):
                        return cells[idx]
            return ""
        row = MappingRow(line=first + i + 1, number=cell("#"), section=cell("section"),
                         text=cell("text"), start=cell("start"), end=cell("end"))
        for attr in ("start", "end"):
            try:
                setattr(row, attr + "_ms", parse_time(getattr(row, attr)))
            except ValueError:
                problems.append(f"Mapping line {row.line}: {attr.capitalize()} {getattr(row, attr)!r} "
                                f"is not a time (m:ss.sss)")
        rows.append(row)
    return rows, header, problems


def root_blocks(path: Path) -> dict:
    _, body = split_frontmatter(read_note_text(path))
    out = {}
    for chunk in re.split(r"\n\s*\n", body):
        lines = [l for l in chunk.split("\n") if l.strip()]
        if not lines:
            continue
        m = BLOCK_ID_RE.search(lines[-1])
        if m:
            lines[-1] = BLOCK_ID_RE.sub("", lines[-1])
            out[m.group(1)] = {"first": lines[0].lstrip("# ").strip(),
                               "text": "\n".join(l.lstrip("# ").strip() for l in lines),
                               "heading": lines[0].lstrip().startswith("#")}
    return out


# ------------------------------------------------------------ audio files

_FILE_INDEX = None


def _files_index():
    """Every audio file in the drop folder: (by folder name, by size)."""
    global _FILE_INDEX
    if _FILE_INDEX is None:
        by_folder, by_size = {}, {}
        if FILES_ROOT.is_dir():
            for p in FILES_ROOT.rglob("*"):
                if (p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("._")
                        and "__MACOSX" not in p.parts):
                    folder = p.parent.parent if p.parent.name == "audio" else p.parent
                    by_folder.setdefault(folder.name, []).append(p)
                    by_size.setdefault(p.stat().st_size, []).append(p)
        _FILE_INDEX = (by_folder, by_size)
    return _FILE_INDEX


def _pick(files, declared, size=None):
    files = sorted(set(files))
    for narrow in (lambda f: f.name == declared if declared else False,
                   lambda f: f.stat().st_size == size if isinstance(size, int) else False):
        if len(files) > 1 and any(narrow(f) for f in files):
            files = [f for f in files if narrow(f)]
    if len(files) > 1 and len({f.stat().st_size for f in files}) == 1 and len({f.name for f in files}) == 1:
        return files[0], files[1:]       # the same take filed twice: use one, report the rest
    return (files[0], []) if len(files) == 1 else (None, files)


def locate_file(note: AudioNote, rec: Recording):
    declared = Path(str(rec.declared)).name if rec.declared else None
    near = [p for p in note.path.parent.iterdir()
            if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("._")]
    if note.path.parent != note.text_folder and note.text_folder.is_dir():
        near += [p for p in note.text_folder.iterdir()
                 if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("._")]
    by_folder, by_size = _files_index()
    for how, files in (("next to note", near),
                       ("Audio/ folder", by_folder.get(note.text_folder.name, []))):
        if files:
            got, rest = _pick(files, declared, rec.size_bytes)
            rec.file, rec.candidates = got, rest
            rec.found_by = how if got else None
            return
    if isinstance(rec.size_bytes, int):
        got, rest = _pick(by_size.get(rec.size_bytes, []), declared, rec.size_bytes)
        rec.file, rec.candidates = got, rest
        rec.found_by = "size" if got else None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ parse

def _english_title(fm):
    if fm.get("title_en"):
        return str(fm["title_en"])
    alts = fm.get("alt_titles") or []
    for a in alts if isinstance(alts, list) else [alts]:
        if isinstance(a, dict) and a.get("en"):
            return str(a["en"])
    return None


def _resolve_root(note: AudioNote):
    """The root text is the file `root_text` links to, and only that file: a link to a
    file that does not exist is not guessed around (root_text_title is not used)."""
    if note.root_link:
        p = VAULT / (note.root_link.strip() + ".md")
        if p.is_file():
            note.root_note, note.root_found_by = p, "link"


def parse_note(path, text: str | None = None) -> AudioNote:
    """Parse the note at `path`; pass `text` to parse an edited version not yet written."""
    path = Path(path).resolve()
    text = read_note_text(path) if text is None else text.replace("\r\n", "\n")
    fm, body = split_frontmatter(text)
    body_offset = text[:len(text) - len(body)].count("\n")

    lang = str(fm.get("lang_tag") or "").strip() or None
    title = {}
    if fm.get("title"):
        title[lang or "?"] = str(fm["title"]).strip()
    if _english_title(fm):
        title["en"] = _english_title(fm).strip()

    link = WIKILINK_RE.search(str(fm.get("root_text") or ""))
    note = AudioNote(
        path=path, fm=fm, body=body,
        layout="recordings" if isinstance(fm.get("recordings"), list) else "flat",
        title=title, language=lang,
        license=(str(fm["license"]).strip() if fm.get("license") not in (None, "") else None),
        date=fm.get("date"),
        performance_type=(str(fm.get("performance_type") or "").strip() or None),
        contributors=parse_contributors(fm.get("artist")),
        root_link=link.group(1) if link else None,
        root_title=(str(fm.get("root_text_title") or "").strip() or None),
        dev_ids={k: fm[k] for k in ("dev_audio_id", "dev_recording_id", "dev_uploaded") if fm.get(k)},
    )

    _resolve_root(note)
    if note.root_note:
        try:
            rfm, _ = split_frontmatter(read_note_text(note.root_note))
            note.text_id = (str(rfm.get("text_id") or "").strip() or None)
            note.edition_id = (str(rfm.get("edition_id") or "").strip() or None)
            note.root_blocks = root_blocks(note.root_note)
        except Exception as exc:          # noqa: BLE001 - report, don't crash
            note.problems.append(f"root text {note.root_note.name}: cannot read ({exc})")

    if note.layout == "recordings":
        for i, r in enumerate(fm["recordings"]):
            if not isinstance(r, dict):
                note.problems.append(f"recordings[{i}] is not a mapping")
                continue
            note.recordings.append(Recording(
                key=str(r.get("id") or r.get("version") or f"recording-{i + 1}"),
                declared=r.get("file"), duration_seconds=r.get("duration_seconds"),
                size_bytes=r.get("file_size_bytes"), sha256=r.get("sha256"),
                format_label=r.get("format")))
    elif any(k in fm for k in ("audio_file", "duration_seconds", "sha256", "file_size_bytes")):
        note.recordings.append(Recording(
            key="recitation", declared=fm.get("audio_file"),
            duration_seconds=fm.get("duration_seconds"), size_bytes=fm.get("file_size_bytes"),
            sha256=fm.get("sha256"), format_label=fm.get("format")))
    for rec in note.recordings:
        locate_file(note, rec)

    note.mapping, note.mapping_columns, problems = parse_mapping(body, body_offset)
    note.problems += problems
    return note
