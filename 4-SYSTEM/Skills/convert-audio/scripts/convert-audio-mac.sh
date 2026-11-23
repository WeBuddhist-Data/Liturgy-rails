#!/bin/bash
# Convert WAV/AIFF recordings to AAC (.m4a) on macOS, keeping the exact original length.
#
# Needs nothing installed: uses afconvert and afinfo, which ship with macOS. Apple's
# AAC encoder records the encoder delay and end padding in the .m4a itself, so players
# play exactly the original samples; this script reads the file back and checks that.
#
#   bash convert-audio-mac.sh [-o OUTDIR] [-b 128] [-f] <file or folder> [<more> ...]
#
#   paths  .wav/.aif/.aiff files and/or folders (searched recursively)
#   -o     where the .m4a files go; default: next to each source file. With a folder,
#          its layout is kept under OUTDIR.
#   -b     AAC bitrate in kbps (default 128)
#   -f     re-convert files that already have an .m4a
#
# Output: one OK/SKIP/FAIL block per file, then a summary line. Exit 1 if any failed.

set -u
bitrate=128
outdir=""
force=0

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

while getopts "o:b:fh" opt; do
    case "$opt" in
        o) outdir=${OPTARG%/} ;;
        b) bitrate=$OPTARG ;;
        f) force=1 ;;
        *) usage; exit 2 ;;
    esac
done
shift $((OPTIND - 1))
[ $# -gt 0 ] || { usage; exit 2; }

if [ "$(uname)" != "Darwin" ] || ! command -v afconvert >/dev/null 2>&1; then
    echo "FAIL  this script is for macOS (afconvert not found); on Windows use convert-audio-windows.ps1"
    exit 2
fi
case "$bitrate" in ''|*[!0-9]*) echo "FAIL  -b must be a whole number of kbps"; exit 2 ;; esac

LIMIT=$((50 * 1024 * 1024))   # dev upload limit
done_n=0
skip_n=0
fail_n=0

# ---- afinfo readers ----
rate_of()      { afinfo "$1" 2>/dev/null | sed -n 's/^Data format: *[0-9]* ch, *\([0-9][0-9]*\) Hz.*/\1/p' | head -n 1; }
pcm_frames()   { afinfo "$1" 2>/dev/null | sed -n 's/^audio packets: *\([0-9][0-9]*\).*/\1/p' | head -n 1; }
valid_frames() { afinfo "$1" 2>/dev/null | sed -n 's/^audio \([0-9][0-9]*\) valid frames.*/\1/p' | head -n 1; }
est_seconds()  { afinfo "$1" 2>/dev/null | sed -n 's/^estimated duration: *\([0-9.][0-9.]*\).*/\1/p' | head -n 1; }
mb()           { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1048576 }'; }

fail() { echo "FAIL  $1: $2"; rm -f "$3"; fail_n=$((fail_n + 1)); }

convert_one() {
    src=$1
    dst=$2
    if [ -e "$dst" ] && [ "$force" -eq 0 ]; then
        echo "SKIP  $dst already exists"
        skip_n=$((skip_n + 1))
        return
    fi
    part="$dst.encoding.m4a"
    rate=$(rate_of "$src")
    frames=$(pcm_frames "$src")
    if [ -z "$rate" ] || [ -z "$frames" ]; then
        fail "$src" "macOS cannot read this file as audio" "$part"
        return
    fi
    # AAC tops out at 48 kHz; higher-rate recordings are resampled to 48 kHz
    fmt="aac"
    out_rate=$rate
    if [ "$rate" -gt 48000 ]; then
        fmt="aac@48000"
        out_rate=48000
    fi
    want=$(( (frames * out_rate + rate / 2) / rate ))

    mkdir -p "$(dirname "$dst")" || { fail "$src" "cannot create $(dirname "$dst")" "$part"; return; }
    t0=$(date +%s)
    if ! err=$(afconvert -f m4af -d "$fmt" -b $((bitrate * 1000)) -s 0 -q 127 "$src" "$part" 2>&1); then
        fail "$src" "afconvert: $err" "$part"
        return
    fi

    got=$(valid_frames "$part")
    if [ -n "$got" ]; then
        if [ "$got" != "$want" ]; then
            fail "$src" "length check failed: $got samples, source $want" "$part"
            return
        fi
        len=$(awk -v n="$got" -v r="$out_rate" 'BEGIN { printf "%.3f", n / r }')
    else
        # older afinfo without the "valid frames" line: compare the duration instead
        est=$(est_seconds "$part")
        if [ -z "$est" ] || ! awk -v e="$est" -v n="$want" -v r="$out_rate" 'BEGIN { d = e - n / r; exit !(d < 0.001 && d > -0.001) }'; then
            fail "$src" "length check failed: ${est:-unknown} s, source $(awk -v n="$want" -v r="$out_rate" 'BEGIN { printf "%.3f", n / r }') s" "$part"
            return
        fi
        len=$(awk -v e="$est" 'BEGIN { printf "%.3f", e }')
    fi

    mv -f "$part" "$dst" || { fail "$src" "cannot write $dst" "$part"; return; }
    secs=$(( $(date +%s) - t0 ))
    a=$(stat -f %z "$src")
    b=$(stat -f %z "$dst")
    src_len=$(awk -v n="$frames" -v r="$rate" 'BEGIN { printf "%.3f", n / r }')
    ratio=$(awk -v a="$a" -v b="$b" 'BEGIN { printf "%.1f", a / b }')
    echo "OK    $(basename "$src")"
    echo "      $(mb "$a") MB -> $(mb "$b") MB (${ratio}x smaller, ${secs} s)  length $len s = source $src_len s"
    [ "$rate" != "$out_rate" ] && echo "      note: $rate Hz converted to $out_rate Hz (the AAC maximum)"
    [ "$b" -gt "$LIMIT" ] && echo "      WARNING: still over the 50 MB upload limit; try -b 96 or -b 64"
    echo "      $dst"
    done_n=$((done_n + 1))
}

is_audio() {
    case "$1" in
        *.[wW][aA][vV] | *.[aA][iI][fF] | *.[aA][iI][fF][fF]) return 0 ;;
        *) return 1 ;;
    esac
}

found=0
for p in "$@"; do
    if [ -d "$p" ]; then
        base=${p%/}
        # ._name and __MACOSX are macOS zip leftovers, not recordings
        while IFS= read -r -d '' f; do
            is_audio "$f" || continue
            found=1
            rel=${f#"$base"/}
            if [ -n "$outdir" ]; then dst="$outdir/${rel%.*}.m4a"; else dst="${f%.*}.m4a"; fi
            convert_one "$f" "$dst"
        done < <(find "$base" -type f ! -name '._*' ! -path '*/__MACOSX/*' -print0)
    elif [ -f "$p" ]; then
        if is_audio "$p"; then
            found=1
            name=$(basename "$p")
            if [ -n "$outdir" ]; then dst="$outdir/${name%.*}.m4a"; else dst="${p%.*}.m4a"; fi
            convert_one "$p" "$dst"
        else
            echo "SKIP  $p: not a .wav/.aiff file (mp3/m4a/flac are already compressed; upload them as they are if under 50 MB)"
        fi
    else
        echo "FAIL  not found: $p"
        fail_n=$((fail_n + 1))
    fi
done
[ "$found" -eq 1 ] || echo "no .wav/.aiff files found"

echo
echo "$done_n converted, $skip_n skipped, $fail_n failed"
[ "$fail_n" -eq 0 ] && [ "$found" -eq 1 ]
