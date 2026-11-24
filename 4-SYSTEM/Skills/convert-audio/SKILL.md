---
name: convert-audio
description: Convert recitation recordings (WAV/AIFF) to small AAC .m4a files with exactly the same length, on Windows or macOS, with nothing to install. Use whenever someone wants to convert, compress or shrink audio recordings, turn WAV into m4a/AAC, or has a recording that is too big to upload (over 50 MB, "413", "file too large").
---

# convert-audio

The library accepts uploads up to about **50 MB**. Studio recordings are
uncompressed WAV, about 23 MB per minute, so most of them are too big.
Converting to AAC (.m4a) makes them about **24× smaller** (a 26-minute,
579 MB WAV becomes 24 MB) with no audible difference for recitation. The
converted file has exactly the same number of samples as the original, so
timestamps and cues made on the WAV stay correct.

The conversion runs on the person's own computer, using the encoder that
comes with the operating system. Nothing to install, and the audio never
goes to any website. Your job is only to run the right script and explain
the result: never open or read the audio files themselves.

```
scripts/convert-audio-windows.ps1   ← Windows (Media Foundation)
scripts/convert-audio-mac.sh        ← macOS (afconvert)
```

(Paths are relative to this SKILL.md; in the vault it is
`4-SYSTEM/Skills/convert-audio/`.)

## 1. Which computer is this?

Use the platform from your environment info:

| Platform | Run |
|----------|-----|
| `win32` / Windows | the Windows script |
| `darwin` / macOS | the Mac script |
| `linux` or anything else | stop: tell the person this only works on Windows or Mac |

If you cannot tell, run `uname`: it prints `Darwin` on a Mac and fails on
Windows.

## 2. Which files?

If the person has not said, ask which recording or folder to convert. A
folder is searched through all its subfolders. The `.m4a` is written next to
each original unless they want the files somewhere else (`-OutDir` / `-o`).
The originals are never changed, moved or deleted.

Only WAV (and AIFF on Mac) are converted. MP3, M4A and FLAC are already
compressed: converting again would only lose quality. If one of those is
over 50 MB, tell the person instead of converting it.

## 3. Run it

**Windows** — always `powershell` (Windows PowerShell, built in), never
`pwsh`. `-ExecutionPolicy Bypass` applies to this one run only and changes
no setting. Options go before the paths; quote every path.

```
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill folder>\scripts\convert-audio-windows.ps1" "<file or folder>"
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill folder>\scripts\convert-audio-windows.ps1" -OutDir "<output folder>" "<file or folder>" "<another>"
```

**macOS**

```
bash "<skill folder>/scripts/convert-audio-mac.sh" "<file or folder>"
bash "<skill folder>/scripts/convert-audio-mac.sh" -o "<output folder>" "<file or folder>" "<another>"
```

| Option | Windows | Mac | Default |
|--------|---------|-----|---------|
| output folder | `-OutDir` | `-o` | next to each original |
| bitrate (kbps) | `-Bitrate` | `-b` | 128 |
| redo existing .m4a | `-Force` | `-f` | skip them |

Keep 128 kbps unless the person asks otherwise or a file is still over
50 MB (only for recordings over about 50 minutes; then 96, or 64 for speech).
Conversion takes about 10 seconds per 25 minutes of audio; for a big folder
allow several minutes (run it in the background or with a long timeout).
Running it again skips files already converted, so an interrupted run can
simply be restarted.

## 4. Read the output

```
OK    ཆགས་མེད་བདེ་སྨོན།.WAV
      578.7 MB -> 24.4 MB (23.7x smaller, 11 s)  length 1580.140 s = source 1580.140 s
      D:\Recordings\ཆགས་མེད་བདེ་སྨོན།.m4a

1 converted, 0 skipped, 0 failed
```

| Line | Meaning |
|------|---------|
| `OK` | converted; the length was read back from the new file and matches the original exactly |
| `SKIP … already exists` | converted before; `-Force` / `-f` to redo |
| `SKIP … not a .wav` | already compressed; nothing to do |
| `FAIL … length check failed` | the new file was deleted; nothing is left half-done. Report it: do not retry with other settings |
| `FAIL … cannot read / convert` | the file is damaged or not really audio |
| `note: … Hz converted to …` | a high sample-rate recording was brought down to 48 kHz, which AAC needs |
| `WARNING: still over the 50 MB` | a very long recording; offer a lower bitrate |
| `run this with Windows PowerShell` | you used `pwsh`; use `powershell` |

## 5. Report back

In plain words, for someone who is not technical: how many files were
converted, a short list of name / size before → after, where the new files
are, and every `FAIL` line quoted with what it means. Remind them to **keep
the original WAVs** (they are the master copies); the `.m4a` files are the
ones to upload.

Do not upload, rename, move or delete anything unless the person asks for
that separately.

## Why "same length" needs care

AAC stores sound in blocks of 1024 samples, so encoders pad the last block
with silence (that 1580.140 s WAV would otherwise come out as 1580.160 s).
Both scripts make the .m4a record where the real audio ends, and then check
it, so players and the library see the exact original length.
