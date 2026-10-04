"""Create a catalog skeleton for a new farmer language.

  python3 scripts/new_language.py luy "Luhya" "Oluluhya"

Writes pestwatch/i18n/catalogs/<code>.json with every key copied from English
and ``meta.review_status = "untranslated"``. Untranslated catalogs are never
served to farmers (requests fall back to English), so the file can be committed
and translated incrementally. When translators and an extension officer have
reviewed it, set review_status to e.g. "reviewed" (or "draft" for pilots) and
fill in ``intents`` with the words farmers actually use. Then run the tests.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_DIR = os.path.join(HERE, "..", "pestwatch", "i18n", "catalogs")


def skeleton(code: str, name: str, native_name: str, catalog_dir: str = CATALOG_DIR) -> dict:
    with open(os.path.join(catalog_dir, "en.json"), encoding="utf-8") as f:
        en = json.load(f)
    cat = {k: v for k, v in en.items() if k not in ("meta", "intents")}
    cat["meta"] = {"code": code, "name": name, "native_name": native_name, "fallback": "en",
                   "review_status": "untranslated", "decimal": ".",
                   "aliases": sorted({code, name.lower(), native_name.lower()})}
    # Keywords must come from how farmers actually write; English words are not copied.
    cat["intents"] = {intent: [] for intent in en["intents"]}
    return {"meta": cat.pop("meta"), **cat}


def write(code, name, native_name, catalog_dir=CATALOG_DIR, force=False) -> str:
    if not code.isalpha() or not 2 <= len(code) <= 8:
        raise ValueError("language code must be 2-8 letters (ISO 639)")
    path = os.path.join(catalog_dir, f"{code.lower()}.json")
    if os.path.exists(path) and not force:
        raise FileExistsError(f"{path} exists (use --force to overwrite)")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(skeleton(code.lower(), name, native_name, catalog_dir), f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code")
    ap.add_argument("name")
    ap.add_argument("native_name")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        path = write(a.code, a.name, a.native_name, force=a.force)
    except (ValueError, FileExistsError) as e:
        sys.exit(str(e))
    print(f"Wrote {os.path.relpath(path)} (review_status=untranslated; not served until reviewed)")


if __name__ == "__main__":
    main()
