#!/usr/bin/env python3
"""Send segment id maps to the WeBuddhist studio, which moves its own records (sub-tasks,
tag segments, verse bookmarks, recitation play times, live-edition settings) from each old
segment id to the new one.

    python 4-SYSTEM/Skills/segment-id-remap/scripts/id_remap.py <map files or folders> [--yes]
           [--url URL] [--log FILE]

  <map files or folders>  segment id maps: {"segment": [{"old_segment_id", "new_segment_id"}]}
                          (a folder: every *.json in it except *.detail.json)
  --yes                   send (without it: only lists what would be sent)
  --url                   default https://studio.webuddhist.com/api/v1/cms/admin/id-remap/segments
  --log FILE              also write every response to FILE (JSON)

One POST per map, one at a time, the file's JSON as the body. A response whose
`updated_counts` are all 0 and `skipped` is empty is quiet ("nothing pointed at these
segments"); anything else is printed in full and counted, so it can be reported.

The bearer token is read from the environment variable or the git-ignored `.env`
(vault root, or 4-SYSTEM/scripts/) as `bearer_token`; it is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://studio.webuddhist.com/api/v1/cms/admin/id-remap/segments"
TOKEN_NAMES = ("bearer_token", "BEARER_TOKEN")


def _vault() -> Path:
    """The vault this runs in: $VAULT_ROOT, else the nearest folder (from the current directory
    up) holding both 1-SOURCES/ and 4-SYSTEM/ — the same rule as library-upload."""
    if os.environ.get("VAULT_ROOT"):
        return Path(os.environ["VAULT_ROOT"]).resolve()
    for start in (Path.cwd().resolve(), Path(__file__).resolve()):
        for d in [start, *start.parents]:
            if (d / "1-SOURCES").is_dir() and (d / "4-SYSTEM").is_dir():
                return d
    return Path.cwd().resolve()


VAULT = _vault()


def token() -> str | None:
    for n in TOKEN_NAMES:
        if os.environ.get(n):
            return os.environ[n]
    for dotenv in (VAULT / ".env", VAULT / "4-SYSTEM" / "scripts" / ".env"):
        if not dotenv.is_file():
            continue
        values = {}
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            k, _, v = line.strip().removeprefix("export ").partition("=")
            values[k.strip()] = v.strip().strip("\"'")
        for n in TOKEN_NAMES:
            if values.get(n):
                return values[n].removeprefix("Bearer ").strip()
    return None


def maps(paths) -> list:
    out = []
    for p in map(Path, paths):
        files = sorted(p.glob("*.json")) if p.is_dir() else [p]
        out += [f for f in files if not f.name.endswith(".detail.json")]
    return out


def load(f: Path) -> dict:
    data = json.loads(f.read_text(encoding="utf-8"))
    seg = data.get("segment") if isinstance(data, dict) else None
    if not isinstance(seg, list) or not all(isinstance(x, dict) and x.get("old_segment_id") and x.get("new_segment_id")
                                            for x in seg):
        raise ValueError('not a segment id map: expected {"segment": [{"old_segment_id", "new_segment_id"}, …]}')
    return data


def post(url, tok, body) -> tuple:
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                                          "Accept": "application/json", "User-Agent": "segment-id-remap/1"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text[:500]


def quiet(resp) -> bool:
    return (isinstance(resp, dict) and not resp.get("skipped")
            and all(v == 0 for v in (resp.get("updated_counts") or {}).values()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="segment id map files or folders")
    ap.add_argument("--yes", action="store_true", help="send (without it nothing is sent)")
    ap.add_argument("--url", default=URL)
    ap.add_argument("--log", help="write every response to this JSON file")
    a = ap.parse_args(argv)

    files = maps(a.paths)
    if not files:
        raise SystemExit("no segment id maps found in the given paths")
    tok = token()
    if a.yes and not tok:
        raise SystemExit("no bearer token: add bearer_token=… to the git-ignored .env (never paste it into chat)")

    log, noted, failed = [], [], 0
    for i, f in enumerate(files, 1):
        try:
            body = load(f)
        except (ValueError, OSError) as exc:
            print(f"[{i}/{len(files)}] SKIP {f.name}: {exc}")
            failed += 1
            continue
        n = len(body["segment"])
        if not a.yes:
            print(f"[{i}/{len(files)}] would POST {n} segment(s)  {f}")
            continue
        status, resp = post(a.url, tok, body)
        log.append({"file": str(f), "status": status, "response": resp})
        if not 200 <= status < 300:
            print(f"[{i}/{len(files)}] FAILED {status} {f.name}: {json.dumps(resp, ensure_ascii=False)[:500]}")
            failed += 1
            break                                   # stop: the next one would most likely fail the same way
        if quiet(resp):
            print(f"[{i}/{len(files)}] ok, requested {resp.get('requested')}, nothing to update  {f.name}")
        else:
            noted.append((f, resp))
            print(f"[{i}/{len(files)}] UPDATED / SKIPPED  {f.name}\n{json.dumps(resp, ensure_ascii=False, indent=1)}")
    if a.log and log:
        Path(a.log).parent.mkdir(parents=True, exist_ok=True)
        Path(a.log).write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.yes:
        print(f"\n{len(log)} sent, {len(noted)} with updates or skips, {failed} failed"
              + (f"; log {a.log}" if a.log and log else ""))
        for f, resp in noted:
            print(f"  {f}: updated {resp.get('updated_counts')}, skipped {resp.get('skipped')}")
    else:
        print(f"\n{len(files)} map(s) would be sent (dry run: add --yes)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
