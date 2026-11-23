#!/usr/bin/env python3
"""Point audio notes at the AAC (.m4a) made from their WAV (skill convert-audio).

For every recording in 1-SOURCES/Audio whose note declares a .wav and whose
converted .m4a sits next to the WAV, this:

  1. checks the WAV is the file the note describes (sha256) and the .m4a has the
     note's duration (the converter keeps the exact sample length);
  2. rewrites the recording to describe the .m4a: file name (the folder part of the
     path is kept as written), format, codec, bit rate, size, sha256; bit_depth goes
     (AAC has none);
  3. keeps the WAV's details under `converted_from:` (it stays the master copy);
  4. updates the body: the file table row and the ![[…]] player embed.

Notes already pointing at an .m4a are left alone, so running it twice changes
nothing. Line endings are kept. Dry run unless --write.

Usage (from the vault root):
    python 4-SYSTEM/Skills/library-upload/scripts/audio_notes_to_m4a.py [NOTE_OR_FOLDER ...] [--write] [--bitrate 128]
"""
from __future__ import annotations

import argparse
import datetime
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "linter-audio"))
import audio_note as an  # noqa: E402
import lint_audio as la  # noqa: E402

FIELDS = ("format", "codec", "bit_depth", "bit_rate_kbps", "file_size_bytes", "sha256")


def m4a_seconds(path: Path) -> float:
    """Played length of an .m4a: mvhd duration / timescale (honours the edit list the converter writes)."""
    with open(path, "rb") as f:
        size_all = path.stat().st_size
        pos = 0
        while pos + 8 <= size_all:
            f.seek(pos)
            size, typ = struct.unpack(">I4s", f.read(8))
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            if typ == b"moov":
                f.seek(pos + 8)
                moov = f.read(size - 8)
                i = moov.find(b"mvhd")
                p = moov[i + 4:]
                if p[0] == 1:
                    ts, dur = struct.unpack(">IQ", p[20:32])
                else:
                    ts, dur = struct.unpack(">II", p[12:20])
                return dur / ts
            pos += size
    raise ValueError("no moov box")


def block_range(lines, fm_end, layout, declared):
    """(first, last, indent) of the frontmatter lines describing this recording."""
    if layout == "flat":
        return 1, fm_end, ""
    start = next(i for i in range(1, fm_end)
                 if re.match(r"\s+(-\s+)?file:", lines[i]) and str(declared) in lines[i])
    indent = re.match(r"\s*", lines[start].replace("- ", "  ", 1)).group(0)
    first = start
    while not re.match(r"\s*-\s", lines[first]):
        first -= 1
    last = first + 1
    while last < fm_end and (lines[last].startswith(indent) and not re.match(r"\s*-\s", lines[last])):
        last += 1
    return first, last, indent


def rewrite(path: Path, rec, m4a: Path, digest: str, bitrate: int):
    raw = path.read_bytes().decode("utf-8-sig")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    fm_end = lines.index("---", 1)
    n_layout = "recordings" if any(l.startswith("recordings:") for l in lines[:fm_end]) else "flat"
    first, last, indent = block_range(lines, fm_end, n_layout, rec.declared)

    def field_line(key):
        pat = re.compile(r"^(" + re.escape(indent) + r"(?:-\s+)?)" + key + r":\s*(.*)$")
        for i in range(first, last):
            m = pat.match(lines[i])
            if m:
                return i, m.group(2)
        return None, None

    old = {k: field_line(k)[1] for k in FIELDS}
    if old["format"] is None or old["file_size_bytes"] is None or old["sha256"] is None:
        raise ValueError("format / file_size_bytes / sha256 not found in the note")
    quote = old["format"].startswith('"')

    def val(s):
        return f'"{s}"' if quote else s

    wav_name = Path(str(rec.declared)).name
    m4a_name = Path(wav_name).stem + ".m4a"
    file_key = "audio_file" if n_layout == "flat" else "file"
    file_i, file_raw = field_line(file_key)

    # 1. new values for the recording
    new = {"format": val("M4A (AAC)"), "codec": val("AAC-LC"), "bit_rate_kbps": str(bitrate),
           "file_size_bytes": str(m4a.stat().st_size), "sha256": digest}
    for k, v in new.items():
        i, _ = field_line(k)
        if i is not None:
            lines[i] = re.sub(r":\s*.*$", ": " + v.replace("\\", "\\\\"), lines[i], count=1)
    i, _ = field_line("bit_depth")
    if i is not None:
        del lines[i]
        last -= 1
        fm_end -= 1

    # 2. the WAV, kept as the master copy
    sha_i, _ = field_line("sha256")
    master = [f"{indent}converted_from:", f"{indent}  {file_key}: {file_raw}"]
    master += [f"{indent}  {k}: {old[k]}" for k in FIELDS if old[k] is not None]
    master.append(f"{indent}  converted: {val(f'{datetime.date.today()}, AAC {bitrate} kbps, same length to the sample')}")
    lines[sha_i + 1:sha_i + 1] = master
    fm_end += len(master)

    # 3. the file name everywhere else (frontmatter path, file table, player embed)
    for i, line in enumerate(lines):
        if i in range(sha_i + 1, sha_i + 1 + len(master)):
            continue
        if wav_name in line:
            line = line.replace(wav_name, m4a_name)
            if line.lstrip().startswith("|"):
                line = re.sub(r"WAV,[^|]*?/\s*", f"AAC {bitrate} kbps / ", line)
            lines[i] = line
    return nl.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="notes or folders (default: 1-SOURCES/Audio)")
    ap.add_argument("--write", action="store_true", help="write the notes (default: dry run)")
    ap.add_argument("--bitrate", type=int, default=128, help="the bitrate the .m4a files were made with")
    ap.add_argument("--audio-dir", help="folder holding the downloaded audio files")
    a = ap.parse_args(argv)
    if a.audio_dir:
        an.set_files_root(a.audio_dir)

    changed = skipped = failed = 0
    for path in la.collect(a.paths):
        rel = path.relative_to(an.VAULT)
        n = an.parse_note(path)
        for rec in n.recordings:
            if Path(str(rec.declared or "")).suffix.lower() != ".wav":
                continue
            if rec.file is None or rec.file.suffix.lower() != ".wav":
                print(f"SKIP  {rel}: the WAV it describes is not found")
                skipped += 1
                continue
            m4a = rec.file.with_suffix(".m4a")
            if not m4a.is_file():
                print(f"SKIP  {rel}: no {m4a.name} next to the WAV (convert it first)")
                skipped += 1
                continue
            try:
                if rec.sha256 and an.sha256_of(rec.file) != str(rec.sha256).lower():
                    raise ValueError(f"{rec.file.name} is not the file the note describes (sha256 differs)")
                secs = m4a_seconds(m4a)
                if isinstance(rec.duration_seconds, (int, float)) and abs(secs - rec.duration_seconds) > 0.0015:
                    raise ValueError(f"{m4a.name} is {secs:.3f} s, the note says {rec.duration_seconds} s")
                digest = an.sha256_of(m4a)
                text = rewrite(path, rec, m4a, digest, a.bitrate)
                check = an.parse_note(path, text=text)       # still valid YAML, and finds the .m4a
                got = next(r for r in check.recordings if Path(str(r.declared)).suffix.lower() == ".m4a")
                if got.file is None or got.file.resolve() != m4a.resolve() or got.size_bytes != m4a.stat().st_size:
                    raise ValueError("rewritten note does not resolve to the .m4a")
            except Exception as exc:  # noqa: BLE001 - report and go on
                print(f"FAIL  {rel}: {exc}")
                failed += 1
                continue
            if a.write:
                path.write_bytes(text.encode("utf-8"))
            mb = lambda b: b / 1048576  # noqa: E731
            print(f"{'ok   ' if a.write else 'DRY  '} {rel}\n      {rec.file.name} {mb(rec.file.stat().st_size):.1f} MB"
                  f" -> {m4a.name} {mb(m4a.stat().st_size):.1f} MB, {secs:.3f} s")
            changed += 1
    print(f"\n{changed} {'updated' if a.write else 'would be updated'}, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
