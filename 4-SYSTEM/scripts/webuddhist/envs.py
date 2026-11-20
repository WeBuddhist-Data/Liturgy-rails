"""The library's environments. Add one here (e.g. `test`) and every tool can use it.

dev and prod are separate databases. dev was replicated from prod at one point,
so texts from before that share ids; texts created since exist only where they
were created, and audio exists only on dev for now. Ids are therefore always
read per environment (ledgers live in 4-SYSTEM/ledgers/<env>/), never assumed
to carry over.

A `protected` environment needs `--confirm <name>` on top of `--yes` for any
write. Keys are read from the environment variable named in `key_var`, else
from the vault's git-ignored `.env` (`key_dotenv` line), and are never printed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VAULT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Env:
    name: str
    url: str
    protected: bool = False
    key_var: str | None = None        # environment variable holding the API key
    key_dotenv: str | None = None     # `.env` line name holding the API key

    def api_key(self) -> str | None:
        if self.key_var and os.environ.get(self.key_var):
            return os.environ[self.key_var]
        if self.key_dotenv:
            dotenv = VAULT / ".env"
            if dotenv.is_file():
                for line in dotenv.read_text(encoding="utf-8").splitlines():
                    k, _, v = line.partition("=")
                    if k.strip() == self.key_dotenv:
                        return v.strip().strip("\"'") or None
        return None

    def headers(self) -> dict:
        key = self.api_key()
        return {"X-API-Key": key} if key else {}


ENVIRONMENTS = {
    "dev": Env("dev", "https://library.webuddhist.com"),
    "prod": Env("prod", "https://library-prod.webuddhist.com", protected=True,
                key_var="WEBUDDHIST_API_KEY_PROD", key_dotenv="api_key"),
}


def get(name: str) -> Env:
    try:
        return ENVIRONMENTS[name]
    except KeyError:
        raise SystemExit(f"unknown environment {name!r}; known: {', '.join(ENVIRONMENTS)}") from None
