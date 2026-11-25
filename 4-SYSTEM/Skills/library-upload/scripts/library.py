#!/usr/bin/env python3
"""Keep the WeBuddhist library in step with a rails vault, per environment.

    python 4-SYSTEM/Skills/library-upload/scripts/library.py \
        <text|translation|commentary|audio> <command> --env <dev|testing|prod|…> [NOTES_OR_FOLDERS ...] [options]

Run from the vault root. Kinds (default folders; override in 4-SYSTEM/library-upload.json):
  text         1-SOURCES/Text          root texts
  translation  1-SOURCES/Translations  aligned to the root from transclusions (or matching block ids)
  commentary   1-SOURCES/Commentaries  aligned commentary -> root from transclusions
  audio        1-SOURCES/Audio         audio notes: audio, recordings, cues (only where the API has /v2/audios)

Commands:
  status   each note's state on the environment (read-only)
  push     create what is missing          --only (text kinds): text, edition, toc, yigchungs,
                                           durchens, footnotes, alignment;  (audio): audio, recording, cues
  update   PATCH / re-sync what changed    --only (text kinds): details, content, toc, yigchungs,
                                           durchens, footnotes, ids;  (audio): audio, recording
           (ids: frontmatter ids that are gone from the server get the server's current id)
  replace  text kinds: re-segmentation — DELETE segmentation -> one full content REPLACE ->
           POST segmentation -> annotations again; refused for a linked text unless --dev.
           audio: the file changed -> new recording on the same audio, old one deleted, cues again.
  delete   text kinds: --only alignment|toc|yigchungs|durchens|footnotes|tags (texts never;
           tags: only the tags on the environment that the note's tag_ids do not list; prints a
           warning and sends only with --confirm-remove-tags, after the person has confirmed);
           audio: --only cues|recording|audio. The notes must be named.
  verify   deep read-only check

Every write prints its plan and sends nothing without --yes; a protected environment (prod)
also needs --confirm <env>. Text payloads come from the vault's own linter and parser
(4-SYSTEM/scripts/); read-only commands put back any frontmatter the linter fills in.
Ids: the notes' frontmatter holds one environment's (default prod: text_id, edition_id,
toc_id, aligned_to_edition_id), written only on that environment and never overwritten —
except an id this tool replaced or confirmed gone (state ids-stale), which gets its successor;
every environment's ids are in the git-ignored ledgers 4-SYSTEM/ledgers/<env>/.
"""
from __future__ import annotations

import argparse
import collections
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webuddhist import envs  # noqa: E402
from webuddhist import vault  # noqa: E402
from webuddhist.api import ApiError, Client  # noqa: E402
from webuddhist.ledger import Ledger  # noqa: E402
from webuddhist.plan import execute  # noqa: E402

WRITE = {"push", "update", "replace", "delete"}
REMOVE_TAGS_WARNING = """\
WARNING — removing tags from the library
  * A tag on the library that the note does not list may have been attached on purpose outside
    the vault (a tagging pass, or someone correcting it by hand). If the library is right, update
    the note's `tags` / `tag_ids` instead of removing the tag.
  * Removing a tag from a root text also removes it from every translation of that text.
  * Sending needs --yes and --confirm-remove-tags (and --confirm prod on prod), and only after the
    person has seen exactly which tags go and said yes.
"""
TEXT_KINDS = ("text", "translation", "commentary")
ANN = ("toc", "yigchungs", "durchens", "footnotes")
ONLY = {
    ("audio", "push"): (("audio", "recording", "cues"), ("audio", "recording", "cues")),
    ("audio", "update"): (("audio", "recording"), ("audio", "recording")),
    ("audio", "delete"): (("cues", "recording", "audio"), ()),
}
for _k in TEXT_KINDS:
    _push = ("text", "edition", *ANN) + (("alignment",) if _k != "text" else ())
    ONLY[(_k, "push")] = (_push, _push)
    ONLY[(_k, "update")] = (("details", "content", *ANN, "ids"), ("details", "content", *ANN, "ids"))
    ONLY[(_k, "delete")] = ((*ANN, "tags") + (("alignment",) if _k != "text" else ()), ())


def parse_only(kind, command, value):
    allowed, default = ONLY.get((kind, command), ((), ()))
    if not value:
        return set(default)
    got = {x.strip() for v in value for x in v.split(",") if x.strip()}
    bad = got - set(allowed)
    if bad:
        raise SystemExit(f"--only for {kind} {command}: unknown {sorted(bad)}; choose from {', '.join(allowed) or '-'}")
    return got


def api_has(client, path):
    """True / False: the environment's OpenAPI lists `path`; None when the spec cannot be read.

    Each environment runs a different build (dev: the dev branch, testing: main, prod: the last
    tagged release), so an endpoint can exist on one and not yet on another."""
    try:
        spec = client.get("/openapi.json")
    except ApiError:
        return None
    return path in (spec or {}).get("paths", {})


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
        print(f"{', '.join(st.flags):40} {st.title}")
        for flag, detail in st.flags.items():
            if detail and flag != "ok":
                print(f"{'':42}{flag}: {str(detail)[:240]}")


def status_lines(states):
    out = []
    for st in sorted(states, key=lambda s: (sorted(s.flags), s.title)):
        detail = "; ".join(f"{k}: {v}" for k, v in st.flags.items() if v)
        out.append(f"- {st.link}: **{', '.join(st.flags)}**" + (f": {detail}" if detail else ""))
    return out


def write_report(path, title, run_info, sections):
    out = [f"# {title}", "", f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `{run_info}`.", ""]
    for heading, lines in sections:
        out += [f"## {heading}", ""] + list(lines) + [""]
    Path(path).write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("kind", choices=["text", "translation", "commentary", "audio"])
    ap.add_argument("command", choices=["status", "push", "update", "replace", "delete", "verify"])
    ap.add_argument("paths", nargs="*", help="notes or folders (default: every note of that kind)")
    ap.add_argument("--env", required=True, choices=sorted(envs.ENVIRONMENTS))
    ap.add_argument("--only", action="append", help="steps, comma-separated (see the command list)")
    ap.add_argument("--artist-op", help="audio: only notes whose artist carries this [op:ID]")
    ap.add_argument("--audio-dir", help="audio: folder holding the downloaded audio files")
    ap.add_argument("--dev", action="store_true",
                    help="the person running this is a developer: allows re-segmenting a linked text")
    ap.add_argument("--full-replace", action="store_true",
                    help="replace only, with --dev: re-segment every named note (delete segmentation, replace the "
                         "whole content, post segmentation, annotations), even when in-place patching would do. "
                         "Breaks its alignments: push them again afterwards.")
    ap.add_argument("--yes", action="store_true", help="send the plan (without it nothing is sent)")
    ap.add_argument("--confirm", help="repeat the environment's name to write to a protected one (prod)")
    ap.add_argument("--confirm-remove-tags", action="store_true",
                    help="delete --only tags: the person has read the warning and confirmed removing the tags")
    ap.add_argument("--report", nargs="?", const="", metavar="FILE",
                    help="write a Markdown report (default: 4-SYSTEM/reports/<env>/<kind>-<command>.md)")
    a = ap.parse_args(argv)

    env = envs.get(a.env)
    if env.key_dotenv and not env.api_key():
        raise SystemExit(f"{env.name} needs its own API key: add {env.key_dotenv[0]}=… to the git-ignored .env "
                         "(vault root or 4-SYSTEM/scripts/). Never paste it into chat.")
    if a.command in WRITE and a.yes and env.protected and a.confirm != env.name:
        raise SystemExit(f"{env.name} is protected: add --confirm {env.name} to send")
    if a.full_replace and (a.command != "replace" or not a.dev):
        raise SystemExit("--full-replace works only with `replace` and --dev")
    if a.command == "delete":
        if not a.paths:
            raise SystemExit("delete needs the notes named explicitly (no default to everything)")
        if not a.only:
            raise SystemExit("delete needs --only")
    only = parse_only(a.kind, a.command, a.only)
    removing_tags = a.command == "delete" and "tags" in only
    if a.confirm_remove_tags and not removing_tags:
        raise SystemExit("--confirm-remove-tags goes only with `delete --only tags`")
    if removing_tags:
        print(REMOVE_TAGS_WARNING)
        if a.yes and not a.confirm_remove_tags:
            raise SystemExit("Refused: removing tags needs the person's confirmation. Show them the plan "
                             "(run without --yes) and this warning; only after they confirm, add "
                             "--confirm-remove-tags.")
    run_info ="library.py " + " ".join(sys.argv[1:] if argv is None else argv)
    sections, outcome = [], None
    sending = a.command in WRITE and a.yes

    if a.kind == "audio":
        if api_has(Client(env), "/v2/audios/{audio_id}") is False:
            raise SystemExit(f"{env.name} has no audio endpoints yet: it runs a build from before audio "
                             f"(see {env.url}/__/health). Audio goes to the environments that have it "
                             "(dev, testing) until a release with it is tagged for prod.")
        from webuddhist import audio as A
        if a.audio_dir:
            A.an.set_files_root(a.audio_dir)
        ctx = A.Ctx(env=env, client=Client(env), ledger=Ledger(env.name, "audio"))
        states = A.assess_all(a.paths, ctx, artist_op=a.artist_op)
        mod, fix = A, A.FIX
    else:
        from webuddhist import text as T
        ctx = T.Ctx(env=env, client=Client(env), ledger=Ledger(env.name, "texts"), dev=a.dev,
                    full_replace=a.full_replace)
        states = T.assess_all(a.kind, a.paths, ctx, keep_edits=sending)
        mod, fix = T, T.FIX

    if a.command == "status":
        print_status(states, env.name, fix)
        sections.append(("State per note", status_lines(states)))
    elif a.command == "verify":
        checked = mod.verify(states, ctx)
        bad = [(st, p) for st, p in checked if p]
        for st, probs in bad:
            print("BAD   " + st.title + "".join(f"\n        {str(x)[:240]}" for x in probs))
        print(f"\n{len(checked)} checked on {env.name}: {len(checked) - len(bad)} ok, {len(bad)} with problems")
        sections.append((f"Problems ({len(bad)} of {len(checked)})",
                         [f"- {st.link}: " + "; ".join(map(str, p)) for st, p in bad] or ["None."]))
    else:
        planner = {"push": lambda: mod.plan_push(states, ctx, only),
                   "update": lambda: mod.plan_update(states, ctx, only),
                   "replace": lambda: mod.plan_replace(states, ctx),
                   "delete": lambda: mod.plan_delete(states, ctx, only)}[a.command]
        outcome = execute(planner(), yes=a.yes)
        verb = "sent" if a.yes else "planned (dry run: add --yes to send)"
        print(f"\n{a.kind} {a.command} on {env.name}: {outcome['planned']} note(s) {verb}"
              + (f", {outcome['done']} done, {outcome['failed']} failed" if a.yes else "")
              + f", {outcome['problems']} not possible, {sum('blocked' in st.flags for st in states)} blocked")
        sections.append(("Sent" if a.yes else "Plan (dry run)", outcome["lines"] or ["Nothing to do."]))

    if a.report is not None:
        path = Path(a.report) if a.report else vault.REPORTS / env.name / f"{a.kind}-{a.command}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if a.kind == "audio":
            mod.la.write_report(path, [st.res for st in states], title=f"Audio {a.command} on {env.name}",
                                run_info=run_info, sections=sections)
        else:
            write_report(path, f"{a.kind.capitalize()} {a.command} on {env.name}", run_info, sections)
        print(f"report: {path}")
    return 1 if outcome and outcome["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
