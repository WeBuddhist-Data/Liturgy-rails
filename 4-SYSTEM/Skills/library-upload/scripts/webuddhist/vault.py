"""Where things are in the vault this runs in.

The skill is installed into a vault at 4-SYSTEM/Skills/library-upload/ and run from
the vault root. Everything vault-specific comes from here, so the skill itself
stays the same in every vault:

  VAULT            $VAULT_ROOT, else the nearest folder (from the current directory up)
                   holding both 1-SOURCES/ and 4-SYSTEM/
  CONFIG           4-SYSTEM/library-upload.json, optional:
                     {
                       "frontmatter_env": "prod",
                       "folders": {"text": "1-SOURCES/Text", "translation": "1-SOURCES/Translations",
                                   "commentary": "1-SOURCES/Commentaries", "audio": "1-SOURCES/Audio"},
                       "tools": "4-SYSTEM/scripts",
                       "environments": {"test": {"url": "https://…", "protected": false}}
                     }
  FRONTMATTER_ENV  the environment whose ids the notes' frontmatter records
                   (text_id, edition_id, toc_id, aligned_to_edition_id). Default prod:
                   that is what the rails vaults' notes hold. Ids on every other
                   environment live only in the ledgers.
  LEDGERS          4-SYSTEM/ledgers/<env>/ — git-ignored, outside the skill folder
                   (a re-install replaces the skill folder; the ledgers must survive)
  TOOLS            the vault's shared linters and parsers (linter-root-text,
                   parser-root-text, linter-commentary, parser-commentary)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_FOLDERS = {
    "text": "1-SOURCES/Text",
    "translation": "1-SOURCES/Translations",
    "commentary": "1-SOURCES/Commentaries",
    "audio": "1-SOURCES/Audio",
}


def _find_vault() -> Path:
    if os.environ.get("VAULT_ROOT"):
        return Path(os.environ["VAULT_ROOT"]).resolve()
    for start in (Path.cwd().resolve(), Path(__file__).resolve()):
        for d in [start, *start.parents]:
            if (d / "1-SOURCES").is_dir() and (d / "4-SYSTEM").is_dir():
                return d
    raise SystemExit("not inside a vault: run from the vault root (a folder with 1-SOURCES/ and 4-SYSTEM/), "
                     "or set VAULT_ROOT")


VAULT = _find_vault()
CONFIG_FILE = VAULT / "4-SYSTEM" / "library-upload.json"
CONFIG = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.is_file() else {}
FOLDERS = {k: VAULT / v for k, v in {**DEFAULT_FOLDERS, **CONFIG.get("folders", {})}.items()}
FRONTMATTER_ENV = CONFIG.get("frontmatter_env", "prod")
LEDGERS = VAULT / "4-SYSTEM" / "ledgers"
TOOLS = VAULT / CONFIG.get("tools", "4-SYSTEM/scripts")
REPORTS = VAULT / "4-SYSTEM" / "reports"


def rel(path) -> str:
    """Vault-relative posix path (the ledger key of a note)."""
    p = Path(path).resolve()
    try:
        return p.relative_to(VAULT).as_posix()
    except ValueError:
        return p.as_posix()
