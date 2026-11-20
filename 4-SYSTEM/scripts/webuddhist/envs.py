"""The library's environments. Add one here and every tool can use it.

  dev      https://dev.library.webuddhist.com/api       no key
  testing  https://testing.library.webuddhist.com/api   key: testing_api_key
  prod     https://library-prod.webuddhist.com          key: prod_api_key   (protected)

They are separate databases. dev was replicated from prod at one point, so texts from
before that share ids; texts created since exist only where they were created, and
audio exists only on dev for now. Ids are therefore always read per environment
(ledgers live in 4-SYSTEM/ledgers/<env>/), never assumed to carry over.
(dev.library.webuddhist.com/api is the same server as the older library.webuddhist.com.)

A `protected` environment needs `--confirm <name>` on top of `--yes` for any write.
The API key (sent as X-API-Key) is read, in order, from the environment variables
WEBUDDHIST_API_KEY_<ENV> and <ENV>_API_KEY, then from the vault's git-ignored `.env`
(`testing_api_key=…`, `prod_api_key=…`; the older single `api_key=` line still counts
for prod). Each environment reads only its own names, so a prod key is never sent to
dev or testing. Keys are never printed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

VAULT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Env:
    name: str
    url: str
    protected: bool = False
    key_dotenv: tuple = field(default_factory=tuple)    # `.env` names holding this environment's key

    def _names(self):
        return [f"WEBUDDHIST_API_KEY_{self.name.upper()}", f"{self.name.upper()}_API_KEY", *self.key_dotenv]

    def api_key(self) -> str | None:
        for n in self._names():
            if os.environ.get(n):
                return os.environ[n]
        dotenv = VAULT / ".env"
        if dotenv.is_file():
            values = {}
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                k, _, v = line.strip().removeprefix("export ").partition("=")
                values[k.strip()] = v.strip().strip("\"'")
            for n in self._names():
                if values.get(n):
                    return values[n]
        return None

    def headers(self) -> dict:
        key = self.api_key()
        return {"X-API-Key": key} if key else {}


ENVIRONMENTS = {
    "dev": Env("dev", "https://dev.library.webuddhist.com/api"),
    "testing": Env("testing", "https://testing.library.webuddhist.com/api", key_dotenv=("testing_api_key",)),
    "prod": Env("prod", "https://library-prod.webuddhist.com", protected=True,
                key_dotenv=("prod_api_key", "api_key")),
}


def get(name: str) -> Env:
    try:
        return ENVIRONMENTS[name]
    except KeyError:
        raise SystemExit(f"unknown environment {name!r}; known: {', '.join(ENVIRONMENTS)}") from None
