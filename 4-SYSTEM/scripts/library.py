#!/usr/bin/env python3
"""Upload to the WeBuddhist library and keep it in step with the vault.

    library.py <audio|text|translation|commentary> <command> --env <dev|prod> [NOTES_OR_FOLDERS ...] [options]

Kinds: text = root texts (1-SOURCES/Text); translation = 3-TRANSFORMATIONS/Translations
(aligned root -> translation by block position); commentary = 1-SOURCES/Commentary
(`commentary_of` the root, aligned commentary -> root from its ![[…#^id]] transclusions);
audio = 1-SOURCES/Audio.

Commands:

    status   each note's state on the environment (read-only)
    push     create what is missing                  --only, per kind:
               audio:        audio, recording, cues        (default all)
               text:         text, edition                 (default both)
               translation / commentary:  text, edition, alignment (default all)
    update   PATCH what changed in place             --only
               audio:        audio, recording
               text / translation / commentary:  details, content (content only
                             when every segment boundary is kept)
    replace  audio: the local file changed -> new recording, old one deleted
             text / translation: re-segmentation, one fixed sequence:
               DELETE segmentation -> PATCH content (one full REPLACE, if it
               changed) -> POST segmentation. Refused for a linked text
               (translations, commentaries, alignments, recordings with cues,
               or itself a translation/commentary) unless --dev.
    delete   audio: --only cues|recording|audio;  translation / commentary: --only alignment.
             Texts are never deleted. The notes must be named.
    verify   deep read-only check of what is on the environment

Every write command prints its plan and sends nothing without --yes. A protected
environment (prod) also needs --confirm <env>. --env is always required: dev, prod
(later test) are separate databases; ids live in 4-SYSTEM/ledgers/<env>/.
--dev states that the person running it is a developer (needed to re-segment a
linked text); never pass it on anyone else's behalf.

Examples (from the vault root):
    python 4-SYSTEM/scripts/library.py text status --env dev
    python 4-SYSTEM/scripts/library.py translation push --env dev --only alignment "3-TRANSFORMATIONS/Translations/Gemini/hi"
    python 4-SYSTEM/scripts/library.py audio push --env dev --only cues "1-SOURCES/Audio/<text>" --yes
"""
from __future__ import annotations

import argparse
import collections
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webuddhist import audio as A  # noqa: E402
from webuddhist import envs  # noqa: E402
from webuddhist import text as T  # noqa: E402
from webuddhist.api import Client  # noqa: E402
from webuddhist.ledger import Ledger  # noqa: E402
from webuddhist.plan import execute  # noqa: E402

WRITE = {"push", "update", "replace", "delete"}
REPORTS = envs.VAULT / "4-SYSTEM" / "reports"
ONLY = {
    ("audio", "push"): (("audio", "recording", "cues"), ("audio", "recording", "cues")),
    ("audio", "update"): (("audio", "recording"), ("audio", "recording")),
    ("audio", "delete"): (("cues", "recording", "audio"), ()),
    ("text", "push"): (("text", "edition"), ("text", "edition")),
    ("text", "update"): (("details", "content"), ("details", "content")),
    ("translation", "push"): (("text", "edition", "alignment"), ("text", "edition", "alignment")),
    ("translation", "update"): (("details", "content"), ("details", "content")),
    ("translation", "delete"): (("alignment",), ()),
    ("commentary", "push"): (("text", "edition", "alignment"), ("text", "edition", "alignment")),
    ("commentary", "update"): (("details", "content"), ("details", "content")),
    ("commentary", "delete"): (("alignment",), ()),
}


def parse_only(kind, command, value):
    allowed, default = ONLY.get((kind, command), ((), ()))
    if not value:
        return set(default)
    got = {x.strip() for v in value for x in v.split(",") if x.strip()}
    bad = got - set(allowed)
    if bad:
        raise SystemExit(f"--only for {kind} {command}: unknown {sorted(bad)}; choose from {', '.join(allowed) or '-'}")
    return got


def print_status(states, env, fix):
    counts = collections.Counter(flag for st in states for flag in st.flags)
    print(f"\n{len(states)} notes on {env}:")
    for flag, k in sorted(counts.items(), key=lambda x: (x[0] != "ok", -x[1], x[0])):
        hint = fix.get(flag)
        print(f"  {flag:22} {k:4}" + (f"   -> {hint}" if hint else ""))
    shown = [st for st in states if "ok" not in st.flags]
    if shown:
        print()
    for st in sorted(shown, key=lambda s: (sorted(s.flags), s.title)):
        print(f"{', '.join(st.flags):34} {st.title}")
        for flag, detail in st.flags.items():
            if detail and flag != "ok":
                print(f"{'':36}{flag}: {detail[:240]}")


def status_lines(states):
    lines = []
    for st in sorted(states, key=lambda s: (sorted(s.flags), s.title)):
        detail = "; ".join(f"{k}: {v}" for k, v in st.flags.items() if v)
        lines.append(f"- {st.link}: **{', '.join(st.flags)}**" + (f": {detail}" if detail else ""))
    return lines


def write_report(path, title, run_info, sections):
    out = [f"# {title}", "", f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `{run_info}`.", ""]
    for heading, lines in sections:
        out += [f"## {heading}", ""] + list(lines) + [""]
    Path(path).write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("kind", choices=["audio", "text", "translation", "commentary"])
    ap.add_argument("command", choices=["status", "push", "update", "replace", "delete", "verify"])
    ap.add_argument("paths", nargs="*", help="notes or folders (default: every note of that kind)")
    ap.add_argument("--env", required=True, choices=sorted(envs.ENVIRONMENTS))
    ap.add_argument("--only", action="append", help="steps, comma-separated (see the command list)")
    ap.add_argument("--artist-op", help="audio: only notes whose artist carries this [op:ID]")
    ap.add_argument("--audio-dir", help="audio: folder holding the downloaded audio files")
    ap.add_argument("--dev", action="store_true",
                    help="the person running this is a developer: allows re-segmenting a linked text")
    ap.add_argument("--yes", action="store_true", help="send the plan (without it nothing is sent)")
    ap.add_argument("--confirm", help="repeat the environment's name to write to a protected one (prod)")
    ap.add_argument("--report", nargs="?", const="", metavar="FILE",
                    help="write a Markdown report (default: 4-SYSTEM/reports/<env>/<kind>-<command>.md)")
    a = ap.parse_args(argv)

    env = envs.get(a.env)
    if a.command in WRITE and a.yes and env.protected and a.confirm != env.name:
        raise SystemExit(f"{env.name} is protected: add --confirm {env.name} to send")
    if a.command == "delete":
        if a.kind == "text":
            raise SystemExit("texts are never deleted (their ids are referenced everywhere)")
        if not a.paths:
            raise SystemExit("delete needs the notes named explicitly (no default to everything)")
        if not a.only:
            raise SystemExit("delete needs --only")
    only = parse_only(a.kind, a.command, a.only)
    run_info = "library.py " + " ".join(sys.argv[1:] if argv is None else argv)
    sections, outcome = [], None

    if a.kind == "audio":
        if a.audio_dir:
            A.an.set_files_root(a.audio_dir)
        ctx = A.Ctx(env=env, client=Client(env), ledger=Ledger(env.name, "audio"))
        states = A.assess_all(a.paths, ctx, artist_op=a.artist_op)
        fix, planners = A.FIX, {"push": lambda: A.plan_push(states, ctx, only),
                                "update": lambda: A.plan_update(states, ctx, only),
                                "replace": lambda: A.plan_replace(states, ctx),
                                "delete": lambda: A.plan_delete(states, ctx, only)}
    else:
        ctx = T.Ctx(env=env, client=Client(env), ledger=Ledger(env.name, "texts"), dev=a.dev)
        states = T.assess_all(a.kind, a.paths, ctx)
        fix, planners = T.FIX, {"push": lambda: T.plan_push(states, ctx, only),
                                "update": lambda: T.plan_update(states, ctx, only),
                                "replace": lambda: T.plan_replace(states, ctx),
                                "delete": lambda: T.plan_delete(states, ctx, only)}

    if a.command == "status":
        print_status(states, env.name, fix)
        sections.append(("State per note", status_lines(states)))
    elif a.command == "verify":
        checked = A.verify(states, ctx) if a.kind == "audio" else T.verify(states, ctx)
        bad = [(st, p) for st, p in checked if p]
        for st, probs in checked:
            if probs:
                print("BAD   " + st.title + "".join(f"\n        {x[:240]}" for x in probs))
        print(f"\n{len(checked)} checked on {env.name}: {len(checked) - len(bad)} ok, {len(bad)} with problems")
        sections.append((f"Problems ({len(bad)} of {len(checked)})",
                         [f"- {st.link}: " + "; ".join(p) for st, p in bad] or ["None."]))
    else:
        outcome = execute(planners[a.command](), yes=a.yes)
        verb = "sent" if a.yes else "planned (dry run: add --yes to send)"
        print(f"\n{a.kind} {a.command} on {env.name}: {outcome['planned']} note(s) {verb}"
              + (f", {outcome['done']} done, {outcome['failed']} failed" if a.yes else "")
              + f", {outcome['problems']} not possible"
              + f", {sum('blocked' in st.flags for st in states)} blocked")
        sections.append(("Sent" if a.yes else "Plan (dry run)", outcome["lines"] or ["Nothing to do."]))

    if a.report is not None:
        path = Path(a.report) if a.report else REPORTS / env.name / f"{a.kind}-{a.command}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if a.kind == "audio":
            A.la.write_report(path, [st.res for st in states], title=f"Audio {a.command} on {env.name}",
                              run_info=run_info, sections=sections)
        else:
            write_report(path, f"{a.kind.capitalize()} {a.command} on {env.name}", run_info, sections)
        print(f"report: {path}")
    return 1 if outcome and outcome["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
