<#
Convert WAV recordings to AAC (.m4a) on Windows, keeping the exact original length.

Needs nothing installed: it uses Windows PowerShell 5.1 and the AAC encoder that ships
with Windows (Media Foundation). Run it with `powershell`, not `pwsh` (PowerShell 7
cannot load the Windows media API used here).

    powershell -NoProfile -ExecutionPolicy Bypass -File convert-audio-windows.ps1 `
        [-OutDir <folder>] [-Bitrate 128] [-Force] <wav file or folder> [<more> ...]

paths    .wav files and/or folders (folders are searched recursively for .wav), last
-OutDir  where the .m4a files go; default: next to each .wav. With a folder as -Path,
         the folder layout is kept under -OutDir.
-Bitrate AAC bitrate in kbps (default 128)
-Force   re-convert files that already have an .m4a

Why the length needs fixing: AAC stores audio in blocks of 1024 samples, so the encoder
pads the last block with silence (a 1580.140 s WAV comes out as 1580.160 s). After
encoding, this script writes an MP4 edit list and sets the movie/track durations to the
WAV's exact sample count, which is how MP4 marks padding as "not part of the audio".
The audio data itself is not touched.

Output: one OK/SKIP/FAIL block per file, then a summary line. Exit code 1 if any failed.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)] [string[]] $Path,
    [string] $OutDir = "",
    [int] $Bitrate = 128,
    [switch] $Force
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

if ($PSVersionTable.PSEdition -eq 'Core') {
    Write-Output "FAIL  run this with Windows PowerShell: powershell -ExecutionPolicy Bypass -File ... (not pwsh)"
    exit 2
}

$UploadLimit = 50MB

Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

public static class AudioLength
{
    // ---- WAV: { frames, sampleRate, channels } ----
    public static long[] WavInfo(string path)
    {
        using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            var r = new BinaryReader(f);
            string riff = Encoding.ASCII.GetString(r.ReadBytes(4));
            r.ReadUInt32();
            string wave = Encoding.ASCII.GetString(r.ReadBytes(4));
            if (riff != "RIFF" || wave != "WAVE") throw new Exception("not a WAV file");
            long ch = 0, rate = 0, align = 0;
            while (f.Position + 8 <= f.Length)
            {
                string id = Encoding.ASCII.GetString(r.ReadBytes(4));
                long size = r.ReadUInt32();
                if (id == "fmt ")
                {
                    long start = f.Position;
                    r.ReadUInt16();
                    ch = r.ReadUInt16();
                    rate = r.ReadUInt32();
                    r.ReadUInt32();
                    align = r.ReadUInt16();
                    f.Position = start + size + (size & 1);
                }
                else if (id == "data")
                {
                    if (align == 0) throw new Exception("WAV has no format chunk");
                    long avail = f.Length - f.Position;
                    if (size > avail) size = avail;   // recorder stopped without fixing the header
                    return new long[] { size / align, rate, ch };
                }
                else f.Position += size + (size & 1);
            }
            throw new Exception("WAV has no audio data");
        }
    }

    // ---- big-endian helpers ----
    static uint U32(byte[] b, int o) { return (uint)(b[o] << 24 | b[o + 1] << 16 | b[o + 2] << 8 | b[o + 3]); }
    static ulong U64(byte[] b, int o) { return (ulong)U32(b, o) << 32 | U32(b, o + 4); }
    static void PutU32(byte[] b, int o, uint v) { b[o] = (byte)(v >> 24); b[o + 1] = (byte)(v >> 16); b[o + 2] = (byte)(v >> 8); b[o + 3] = (byte)v; }
    static void PutU64(byte[] b, int o, ulong v) { PutU32(b, o, (uint)(v >> 32)); PutU32(b, o + 4, (uint)v); }

    // ---- MP4 box tree (only the boxes on the path to the timing info are opened) ----
    class Box
    {
        public string Type;
        public byte[] Payload;
        public List<Box> Kids;
        public Box Find(string t)
        {
            if (Kids == null) return null;
            foreach (var k in Kids) if (k.Type == t) return k;
            return null;
        }
        public byte[] Bytes()
        {
            byte[] body;
            if (Kids == null) body = Payload;
            else
            {
                var ms = new MemoryStream();
                foreach (var k in Kids) { var kb = k.Bytes(); ms.Write(kb, 0, kb.Length); }
                body = ms.ToArray();
            }
            var res = new byte[8 + body.Length];
            PutU32(res, 0, (uint)res.Length);
            Encoding.ASCII.GetBytes(Type, 0, 4, res, 4);
            Buffer.BlockCopy(body, 0, res, 8, body.Length);
            return res;
        }
    }

    static readonly HashSet<string> Containers = new HashSet<string> { "moov", "trak", "mdia", "minf", "stbl", "edts" };

    static List<Box> Parse(byte[] buf, int start, int end)
    {
        var res = new List<Box>();
        int pos = start;
        while (pos + 8 <= end)
        {
            long size = U32(buf, pos);
            string type = Encoding.ASCII.GetString(buf, pos + 4, 4);
            int hdr = 8;
            if (size == 1) { size = (long)U64(buf, pos + 8); hdr = 16; }
            else if (size == 0) size = end - pos;
            int bodyStart = pos + hdr, bodyEnd = pos + (int)size;
            var box = new Box { Type = type };
            if (Containers.Contains(type)) box.Kids = Parse(buf, bodyStart, bodyEnd);
            else { box.Payload = new byte[bodyEnd - bodyStart]; Buffer.BlockCopy(buf, bodyStart, box.Payload, 0, box.Payload.Length); }
            res.Add(box);
            pos = bodyEnd;
        }
        return res;
    }

    // top-level boxes: { offset, size } per box, types in a parallel list
    static List<long[]> TopLevel(string path, List<string> types)
    {
        var res = new List<long[]>();
        using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            var h = new byte[16];
            long pos = 0;
            while (pos + 8 <= f.Length)
            {
                f.Position = pos;
                f.Read(h, 0, 16);
                long size = U32(h, 0);
                if (size == 1) size = (long)U64(h, 8);
                else if (size == 0) size = f.Length - pos;
                types.Add(Encoding.ASCII.GetString(h, 4, 4));
                res.Add(new long[] { pos, size });
                pos += size;
            }
        }
        return res;
    }

    static Box ReadMoov(string path, out long moovOff, out long moovSize, out bool mdatAfterMoov)
    {
        var types = new List<string>();
        var boxes = TopLevel(path, types);
        int mi = types.IndexOf("moov");
        if (mi < 0) throw new Exception("no moov box in the encoded file");
        moovOff = boxes[mi][0];
        moovSize = boxes[mi][1];
        mdatAfterMoov = false;
        for (int i = 0; i < types.Count; i++) if (types[i] == "mdat" && boxes[i][0] > moovOff) mdatAfterMoov = true;
        var buf = new byte[moovSize];
        using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            f.Position = moovOff;
            int got = 0;
            while (got < buf.Length) { int n = f.Read(buf, got, buf.Length - got); if (n <= 0) throw new Exception("short read"); got += n; }
        }
        return Parse(buf, 0, buf.Length)[0];
    }

    static long Timescale(Box b) { return b.Payload[0] == 1 ? U32(b.Payload, 20) : U32(b.Payload, 12); }

    static long Duration(Box b, int v0, int v1) { return b.Payload[0] == 1 ? (long)U64(b.Payload, v1) : U32(b.Payload, v0); }

    static void SetDuration(Box b, long value, int v0, int v1)
    {
        if (b.Payload[0] == 1) PutU64(b.Payload, v1, (ulong)value);
        else
        {
            if (value > uint.MaxValue) throw new Exception("recording too long for a version-0 box");
            PutU32(b.Payload, v0, (uint)value);
        }
    }

    static long CodedSamples(Box stbl)
    {
        var p = stbl.Find("stts").Payload;
        long n = U32(p, 4), total = 0;
        for (int i = 0; i < n; i++) total += (long)U32(p, 8 + 8 * i) * U32(p, 12 + 8 * i);
        return total;
    }

    // Give the .m4a an edit list so it plays exactly `frames` samples at `rate`.
    // Returns the number of padding samples hidden.
    public static long TrimToLength(string path, long frames, long rate)
    {
        long moovOff, moovSize;
        bool mdatAfter;
        Box moov = ReadMoov(path, out moovOff, out moovSize, out mdatAfter);
        if (mdatAfter) throw new Exception("unexpected file layout (moov before mdat); not changed");

        Box mvhd = moov.Find("mvhd");
        long movieTs = Timescale(mvhd);
        long exactMovie = (frames * movieTs + rate / 2) / rate;
        if (exactMovie > uint.MaxValue) throw new Exception("recording too long");
        long padding = -1;
        foreach (var trak in moov.Kids)
        {
            if (trak.Type != "trak") continue;
            Box mdia = trak.Find("mdia");
            if (Encoding.ASCII.GetString(mdia.Find("hdlr").Payload, 8, 4) != "soun") continue;
            long mediaTs = Timescale(mdia.Find("mdhd"));
            long exactMedia = (frames * mediaTs + rate / 2) / rate;
            long coded = CodedSamples(mdia.Find("minf").Find("stbl"));
            padding = coded - exactMedia;
            // Media Foundation adds no encoder delay: audio starts at sample 0 and only the
            // tail is padded, by less than one 1024-sample block. Refuse anything else.
            if (padding < 0 || padding >= 1024 * mediaTs / rate)
                throw new Exception("unexpected AAC padding " + padding + " samples (coded " + coded + ", source " + exactMedia + ")");

            var elst = new byte[20];          // version 0, 1 entry: whole media from time 0, rate 1.0
            PutU32(elst, 4, 1);
            PutU32(elst, 8, (uint)exactMovie);
            PutU32(elst, 12, 0);
            PutU32(elst, 16, 0x00010000);
            var edts = new Box { Type = "edts", Kids = new List<Box> { new Box { Type = "elst", Payload = elst } } };
            trak.Kids.RemoveAll(k => k.Type == "edts");
            trak.Kids.Insert(trak.Kids.IndexOf(trak.Find("tkhd")) + 1, edts);
            SetDuration(trak.Find("tkhd"), exactMovie, 20, 28);
        }
        if (padding < 0) throw new Exception("no audio track in the encoded file");
        SetDuration(mvhd, exactMovie, 16, 24);

        // moov is the last box: keep everything before it, write the new moov.
        byte[] newMoov = moov.Bytes();
        string tmp = path + ".trim.tmp";
        using (var src = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var dst = new FileStream(tmp, FileMode.Create, FileAccess.Write))
        {
            var buf = new byte[1 << 22];
            long left = moovOff;
            while (left > 0)
            {
                int n = src.Read(buf, 0, (int)Math.Min(buf.Length, left));
                if (n <= 0) throw new Exception("short read");
                dst.Write(buf, 0, n);
                left -= n;
            }
            dst.Write(newMoov, 0, newMoov.Length);
            src.Position = moovOff + moovSize;
            int m;
            while ((m = src.Read(buf, 0, buf.Length)) > 0) dst.Write(buf, 0, m);
        }
        File.Delete(path);
        File.Move(tmp, path);
        return padding;
    }

    // { played length in seconds, coded length in seconds }, read back from the file
    public static double[] M4aLength(string path)
    {
        long off, size;
        bool after;
        Box moov = ReadMoov(path, out off, out size, out after);
        Box mvhd = moov.Find("mvhd");
        double played = (double)Duration(mvhd, 16, 24) / Timescale(mvhd);
        foreach (var trak in moov.Kids)
        {
            if (trak.Type != "trak") continue;
            Box mdia = trak.Find("mdia");
            double coded = (double)CodedSamples(mdia.Find("minf").Find("stbl")) / Timescale(mdia.Find("mdhd"));
            return new double[] { played, coded };
        }
        throw new Exception("no track");
    }
}
'@

# ---- Windows media API (WinRT) ----
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFolder, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Transcoding.MediaTranscoder, Windows.Media.Transcoding, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime] | Out-Null
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTaskOp = $methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$asTaskActProg = $methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncActionWithProgress`1' } | Select-Object -First 1
function Await($op, [Type] $type) { $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }

function Encode-Aac([string] $src, [string] $dst, [int] $rate, [int] $channels) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($src)) ([Windows.Storage.StorageFile])
    $folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync([IO.Path]::GetDirectoryName($dst))) ([Windows.Storage.StorageFolder])
    $out = Await ($folder.CreateFileAsync([IO.Path]::GetFileName($dst), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
    $profile = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateM4a([Windows.Media.MediaProperties.AudioEncodingQuality]::High)
    $profile.Audio.Bitrate = [uint32]($Bitrate * 1000)
    $profile.Audio.SampleRate = [uint32]$rate
    $profile.Audio.ChannelCount = [uint32]$channels
    $profile.Audio.BitsPerSample = 16
    $tc = New-Object Windows.Media.Transcoding.MediaTranscoder
    $prep = Await ($tc.PrepareFileTranscodeAsync($file, $out, $profile)) ([Windows.Media.Transcoding.PrepareTranscodeResult])
    if (-not $prep.CanTranscode) { throw "Windows cannot convert this file: $($prep.FailureReason)" }
    $task = $asTaskActProg.MakeGenericMethod([double]).Invoke($null, @($prep.TranscodeAsync()))
    $task.Wait(-1) | Out-Null
}

# ---- collect the work ----
$jobs = New-Object System.Collections.Generic.List[object]
foreach ($p in $Path) {
    if (-not (Test-Path -LiteralPath $p)) { Write-Output "FAIL  not found: $p"; continue }
    $item = Get-Item -LiteralPath $p
    if ($item.PSIsContainer) {
        foreach ($w in Get-ChildItem -LiteralPath $item.FullName -Recurse -File | Where-Object { $_.Extension -ieq '.wav' -and -not $_.Name.StartsWith('._') -and $_.FullName -notmatch '\\__MACOSX\\' } | Sort-Object FullName) {
            # ._name / __MACOSX are macOS zip leftovers, not recordings
            if ($OutDir) {
                $rel = $w.FullName.Substring($item.FullName.TrimEnd('\').Length).TrimStart('\')
                $dst = Join-Path $OutDir ([IO.Path]::ChangeExtension($rel, '.m4a'))
            } else { $dst = [IO.Path]::ChangeExtension($w.FullName, '.m4a') }
            $jobs.Add(@($w.FullName, $dst))
        }
    } elseif ($item.Extension -ieq '.wav') {
        if ($OutDir) { $dst = Join-Path $OutDir ($item.BaseName + '.m4a') } else { $dst = [IO.Path]::ChangeExtension($item.FullName, '.m4a') }
        $jobs.Add(@($item.FullName, $dst))
    } else {
        Write-Output "SKIP  $($item.FullName): not a .wav file (mp3/m4a/flac are already compressed; upload them as they are if under 50 MB)"
    }
}
if ($jobs.Count -eq 0) { Write-Output "`nno .wav files found"; exit 1 }

# ---- convert ----
$done = 0; $skipped = 0; $failed = 0
foreach ($job in $jobs) {
    $src = $job[0]; $dst = $job[1]
    if ((Test-Path -LiteralPath $dst) -and -not $Force) { Write-Output "SKIP  $dst already exists"; $skipped++; continue }
    $part = $dst + '.encoding.m4a'
    try {
        $info = [AudioLength]::WavInfo($src)
        $frames = $info[0]; $srcRate = [int]$info[1]; $srcCh = [int]$info[2]
        # the Windows AAC encoder takes 44.1 or 48 kHz, mono or stereo
        $rate = if ($srcRate -eq 44100 -or $srcRate -eq 48000) { $srcRate } else { 48000 }
        $ch = if ($srcCh -le 2) { $srcCh } else { 2 }
        $want = [long][Math]::Round($frames * $rate / $srcRate)
        New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($dst)) | Out-Null
        $t0 = Get-Date
        Encode-Aac $src $part $rate $ch
        $pad = [AudioLength]::TrimToLength($part, $want, $rate)
        $len = [AudioLength]::M4aLength($part)
        $wantSec = $want / $rate
        if ([Math]::Abs($len[0] - $wantSec) -gt (0.5 / $rate)) { throw ("length check failed: {0:F6} s, source {1:F6} s" -f $len[0], $wantSec) }
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
        Move-Item -LiteralPath $part -Destination $dst
        $secs = ((Get-Date) - $t0).TotalSeconds
        $a = (Get-Item -LiteralPath $src).Length; $b = (Get-Item -LiteralPath $dst).Length
        Write-Output ("OK    {0}" -f [IO.Path]::GetFileName($src))
        Write-Output ("      {0:N1} MB -> {1:N1} MB ({2:N1}x smaller, {3:N0} s)  length {4:F3} s = source {5:F3} s" -f ($a / 1MB), ($b / 1MB), ($a / $b), $secs, $len[0], ($frames / $srcRate))
        if ($srcRate -ne $rate -or $srcCh -ne $ch) { Write-Output ("      note: {0} Hz {1} ch converted to {2} Hz {3} ch (what AAC on Windows supports)" -f $srcRate, $srcCh, $rate, $ch) }
        if ($b -gt $UploadLimit) { Write-Output ("      WARNING: still over the 50 MB upload limit; try -Bitrate 96 or 64") }
        Write-Output ("      {0}" -f $dst)
        $done++
    } catch {
        Write-Output ("FAIL  {0}: {1}" -f $src, $_.Exception.Message)
        if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }
        if (Test-Path -LiteralPath ($part + '.trim.tmp')) { Remove-Item -LiteralPath ($part + '.trim.tmp') -Force }
        $failed++
    }
}
Write-Output ("`n{0} converted, {1} skipped, {2} failed" -f $done, $skipped, $failed)
if ($failed) { exit 1 }
