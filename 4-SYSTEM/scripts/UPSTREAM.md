# Vendored from WeBuddhist/bodhisattvacharyavatara-rails

`linter-root-text/` and `parser-root-text/` are vendored copies of the
WeBuddhist library toolchain, not original work in this vault.

| | |
|---|---|
| Source | https://github.com/WeBuddhist/bodhisattvacharyavatara-rails |
| Path | `4-SYSTEM/scripts/{linter,parser}-root-text/` |
| Pinned at | `10ca665f553c959890e8da59e9b0b38eeedec6e9` |
| Vendored | 2026-08-26 |

The initial commit here is **byte-identical to upstream**, so every later
commit is our delta and can be read as a patch series — keep it that way.
Re-sync with `4-SYSTEM/scripts/vendor_sync.sh`, which diffs the working
copy against upstream at any ref.

## Why it is forked

The liturgy corpus is 103 short independent texts, not one long treatise,
and its notes are stamped by `block_ids.py` with a hierarchical id scheme
(`^k-j-n`). Two upstream assumptions do not hold here; both are measured,
not theorised (see `.claude/skills/block-ids/SKILL.md`):

1. `REF_RE` accepts at most one hyphen, so every `^k-j-n` id is dropped as
   "no reference marker" — 74 of 81 blocks lost across the two 3-level texts.
2. `_extract_blocks` splits only on blank lines, so a heading that is not
   preceded by one is swallowed into the previous block, leaking raw `^id`
   and `##` markup into the uploaded content. Silent — no warning.

3. `<small>…</small>` (yigchung, ཡིག་ཆུང — a rubric set in small type) is an
   annotation, not text. Upstream has no notion of it, so the raw tags were
   measured into `content` and into the character spans. The tags are now
   dropped and the text between them kept, before `pos` advances. 7 notes
   carry 13 of them; the live backend already stores `བཀའ་ཐང་བསྡུས་པ།` this
   way, so this makes the parser agree with the backend rather than diverge.

All three are arguably upstream bugs and should be offered back rather than
carried forever.
