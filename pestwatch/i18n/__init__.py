"""Message catalogs for farmer-facing text: lookup with fallback, plurals, numbers.

Catalogs live in ``catalogs/<code>.json`` and share one key structure (enforced
by tests). To add a language: copy ``en.json``, translate every value, set
``meta.fallback`` and ``meta.review_status``, then run the tests. Nothing else
in the code needs to change.
"""
import json
import os
from functools import lru_cache
from typing import Dict, List

CATALOG_DIR = os.path.join(os.path.dirname(__file__), "catalogs")
DEFAULT_LANG = "en"


UNTRANSLATED = "untranslated"


@lru_cache(maxsize=None)
def all_catalogs() -> Dict[str, dict]:
    """Every catalog on disk, including skeletons that are not yet translated."""
    out = {}
    for name in sorted(os.listdir(CATALOG_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(CATALOG_DIR, name), encoding="utf-8") as f:
                c = json.load(f)
            out[c["meta"]["code"]] = c
    return out


@lru_cache(maxsize=None)
def catalogs() -> Dict[str, dict]:
    """Catalogs that may be shown to farmers. A catalog whose ``meta.review_status``
    is "untranslated" (e.g. a fresh skeleton from scripts/new_language.py) is never
    served: requests for it fall back to the default language."""
    return {code: c for code, c in all_catalogs().items()
            if c["meta"].get("review_status") != UNTRANSLATED or code == DEFAULT_LANG}


def languages() -> List[str]:
    return list(catalogs())


def resolve(lang: str) -> str:
    """Map a requested language to one we have (unknown -> default)."""
    lang = (lang or "").lower().split("-")[0]
    return lang if lang in catalogs() else DEFAULT_LANG


def _chain(lang: str) -> List[str]:
    chain, cur = [], resolve(lang)
    while cur and cur not in chain:
        chain.append(cur)
        cur = catalogs()[cur]["meta"].get("fallback")
    if DEFAULT_LANG not in chain:
        chain.append(DEFAULT_LANG)
    return chain


def lookup(key: str, lang: str):
    """Dotted-key lookup, falling back along meta.fallback to English."""
    for code in _chain(lang):
        node = catalogs()[code]
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if node is not None:
            return node
    raise KeyError(key)


def plural_category(n: int, lang: str) -> str:
    # CLDR cardinal rules for the languages we ship (fr treats 0 and 1 as singular).
    if resolve(lang) == "fr":
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


def number(x: float, lang: str, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", catalogs()[resolve(lang)]["meta"].get("decimal", "."))


def t(key: str, lang: str, count: int = None, **kw) -> str:
    v = lookup(key, lang)
    if isinstance(v, dict) and count is not None:
        v = v.get(plural_category(count, lang), v.get("other"))
        kw.setdefault("n", count)
    if not isinstance(v, str):
        raise TypeError(f"{key} is not a string in {lang}")
    return v.format(**kw)


def ui_bundle() -> Dict[str, dict]:
    """Field-app strings for every language, for shipping to the (offline) client."""
    # Replies and intent keywords let the static (serverless) demo parse farmer replies in the browser.
    return {code: {"meta": c["meta"], "ui": c["ui"], "levels": c["alert"]["level"], "reply": c["reply"],
                   "intents": c["intents"]} for code, c in catalogs().items()}
