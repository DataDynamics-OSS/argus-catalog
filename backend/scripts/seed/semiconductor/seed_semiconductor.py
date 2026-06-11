#!/usr/bin/env python3
"""반도체 제조 표준사전 시드 데이터 로딩 스크립트.

Catalog Server Standard API를 호출하여 반도체 Fab 표준 단어/도메인/코드/용어를 일괄 등록한다.

Usage:
    python seed_semiconductor.py                          # localhost:4600
    python seed_semiconductor.py --base-url http://localhost:4600
    python seed_semiconductor.py --dry-run                # API 호출 없이 데이터 검증만
"""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SEED_DIR = Path(__file__).parent
API_PREFIX = "/api/v1/standards"


def load_json(filename: str) -> list | dict:
    path = SEED_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def api_call(base_url: str, method: str, path: str, data: dict | None = None) -> dict | None:
    url = f"{base_url}{API_PREFIX}{path}"
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None
    req = Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=30) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        if e.code == 409 or "already exists" in error_body.lower() or "unique" in error_body.lower():
            return None  # duplicate, skip
        print(f"  ERROR {e.code}: {url}")
        print(f"  {error_body[:300]}")
        return None
    except URLError as e:
        print(f"  CONNECTION ERROR: {e.reason}")
        sys.exit(1)


def seed_dictionary(base_url: str, dry_run: bool) -> int | None:
    """사전 생성. 반환: dictionary_id"""
    data = load_json("dictionary.json")
    print(f"\n{'='*60}")
    print(f"[1/5] Dictionary: {data['dict_name']}")
    print(f"{'='*60}")

    if dry_run:
        print(f"  (dry-run) Would create dictionary: {data['dict_name']}")
        return -1

    # Check if already exists
    existing = api_call(base_url, "GET", "/dictionaries")
    if existing:
        for d in existing:
            if d["dict_name"] == data["dict_name"]:
                print(f"  Already exists: id={d['id']}")
                return d["id"]

    result = api_call(base_url, "POST", "/dictionaries", data)
    if result:
        print(f"  Created: id={result['id']}")
        return result["id"]
    print("  Failed to create dictionary")
    return None


def seed_words(base_url: str, dict_id: int, dry_run: bool) -> dict[str, int]:
    """단어 등록. 반환: {word_name: word_id}"""
    words = load_json("words.json")
    print(f"\n{'='*60}")
    print(f"[2/5] Words: {len(words)} entries")
    print(f"{'='*60}")

    word_map = {}

    if dry_run:
        for w in words:
            word_map[w["word_name"]] = -1
            print(f"  (dry-run) {w['word_abbr']:12s} {w['word_name']:12s} {w['word_english']}")
        return word_map

    # Load existing words
    existing = api_call(base_url, "GET", f"/words?dictionary_id={dict_id}")
    if existing:
        for w in existing:
            word_map[w["word_name"]] = w["id"]

    created = 0
    skipped = 0
    for w in words:
        if w["word_name"] in word_map:
            skipped += 1
            continue

        payload = {**w, "dictionary_id": dict_id}
        result = api_call(base_url, "POST", "/words", payload)
        if result:
            word_map[result["word_name"]] = result["id"]
            created += 1
        else:
            skipped += 1

    print(f"  Created: {created}, Skipped: {skipped}, Total: {len(word_map)}")
    return word_map


def seed_domains(base_url: str, dict_id: int, dry_run: bool) -> dict[str, int]:
    """도메인 등록. 반환: {domain_name: domain_id}"""
    domains = load_json("domains.json")
    print(f"\n{'='*60}")
    print(f"[3/5] Domains: {len(domains)} entries")
    print(f"{'='*60}")

    domain_map = {}

    if dry_run:
        for d in domains:
            domain_map[d["domain_name"]] = -1
            dtype = d["data_type"]
            length = d.get("data_length") or d.get("data_precision", "")
            print(f"  (dry-run) {d['domain_name']:12s} {dtype}({length})")
        return domain_map

    # Load existing domains
    existing = api_call(base_url, "GET", f"/domains?dictionary_id={dict_id}")
    if existing:
        for d in existing:
            domain_map[d["domain_name"]] = d["id"]

    created = 0
    skipped = 0
    for d in domains:
        if d["domain_name"] in domain_map:
            skipped += 1
            continue

        payload = {**d, "dictionary_id": dict_id}
        result = api_call(base_url, "POST", "/domains", payload)
        if result:
            domain_map[result["domain_name"]] = result["id"]
            created += 1
        else:
            skipped += 1

    print(f"  Created: {created}, Skipped: {skipped}, Total: {len(domain_map)}")
    return domain_map


def seed_code_groups(base_url: str, dict_id: int, dry_run: bool) -> dict[str, int]:
    """코드 그룹 + 코드값 등록. 반환: {group_name: group_id}"""
    groups = load_json("code_groups.json")
    print(f"\n{'='*60}")
    total_values = sum(len(g.get("values", [])) for g in groups)
    print(f"[4/5] Code Groups: {len(groups)} groups, {total_values} values")
    print(f"{'='*60}")

    group_map = {}

    if dry_run:
        for g in groups:
            group_map[g["group_name"]] = -1
            values = g.get("values", [])
            print(f"  (dry-run) {g['group_name']} ({len(values)} values)")
            for v in values:
                print(f"    {v['code_value']:12s} {v['code_name']}")
        return group_map

    # Load existing groups
    existing = api_call(base_url, "GET", f"/code-groups?dictionary_id={dict_id}")
    if existing:
        for g in existing:
            group_map[g["group_name"]] = g["id"]

    created_groups = 0
    created_values = 0
    for g in groups:
        values = g.pop("values", [])

        if g["group_name"] not in group_map:
            payload = {**g, "dictionary_id": dict_id}
            result = api_call(base_url, "POST", "/code-groups", payload)
            if result:
                group_map[result["group_name"]] = result["id"]
                created_groups += 1
            else:
                continue

        group_id = group_map[g["group_name"]]

        # Load existing values for this group
        existing_group = api_call(base_url, "GET", f"/code-groups/{group_id}")
        existing_values = set()
        if existing_group and "values" in existing_group:
            existing_values = {v["code_value"] for v in existing_group["values"]}

        for v in values:
            if v["code_value"] in existing_values:
                continue
            result = api_call(base_url, "POST", f"/code-groups/{group_id}/values", v)
            if result:
                created_values += 1

    print(f"  Groups created: {created_groups}, Values created: {created_values}")
    return group_map


def seed_terms(
    base_url: str,
    dict_id: int,
    word_map: dict[str, int],
    domain_map: dict[str, int],
    dry_run: bool,
) -> int:
    """용어 등록. 단어 조합으로 자동 영문명/약어/물리명 생성."""
    terms = load_json("terms.json")
    print(f"\n{'='*60}")
    print(f"[5/5] Terms: {len(terms)} entries")
    print(f"{'='*60}")

    if dry_run:
        ok = 0
        missing_words = []
        for t in terms:
            words_found = all(w in word_map for w in t.get("words", []))
            domain_found = t.get("domain") in domain_map if t.get("domain") else True
            if words_found and domain_found:
                # Build physical name from word abbreviations
                abbrs = []
                for w_name in t["words"]:
                    # Find the word's abbreviation from words.json
                    abbrs.append(word_map.get(w_name, w_name))
                print(f"  (dry-run) {t['term_name']:20s} → words: {t.get('words', [])}")
                ok += 1
            else:
                missing = [w for w in t.get("words", []) if w not in word_map]
                if missing:
                    missing_words.extend(missing)
                domain_ok = "OK" if domain_found else f"MISSING: {t.get('domain')}"
                print(f"  (dry-run) {t['term_name']:20s} → INCOMPLETE (missing words: {missing}, domain: {domain_ok})")
        if missing_words:
            unique_missing = sorted(set(missing_words))
            print(f"\n  WARNING: {len(unique_missing)} words not found in words.json:")
            for m in unique_missing:
                print(f"    - {m}")
        print(f"\n  Valid: {ok}/{len(terms)}")
        return ok

    # Load existing terms
    existing = api_call(base_url, "GET", f"/terms?dictionary_id={dict_id}")
    existing_names = set()
    if existing:
        existing_names = {t["term_name"] for t in existing}

    created = 0
    skipped = 0
    errors = 0

    for t in terms:
        if t["term_name"] in existing_names:
            skipped += 1
            continue

        # Build term_english and term_abbr from word composition
        word_names = t.get("words", [])
        term_english_parts = []
        term_abbr_parts = []

        all_words_found = True
        for w_name in word_names:
            if w_name not in word_map:
                # Single-char words (X, Y) or compound words not in dictionary
                term_english_parts.append(w_name)
                term_abbr_parts.append(w_name)
                continue

        # Use the morpheme analyzer API if words are in the dictionary
        # Otherwise build manually from word data
        words_data = load_json("words.json")
        word_lookup = {w["word_name"]: w for w in words_data}

        for w_name in word_names:
            if w_name in word_lookup:
                w = word_lookup[w_name]
                term_english_parts.append(w["word_english"])
                term_abbr_parts.append(w["word_abbr"])
            else:
                term_english_parts.append(w_name)
                term_abbr_parts.append(w_name)

        term_english = " ".join(term_english_parts)
        term_abbr = "_".join(term_abbr_parts)
        physical_name = term_abbr.lower()

        # Resolve domain_id
        domain_id = None
        if t.get("domain") and t["domain"] in domain_map:
            domain_id = domain_map[t["domain"]]

        payload = {
            "dictionary_id": dict_id,
            "term_name": t["term_name"],
            "term_english": term_english,
            "term_abbr": term_abbr,
            "physical_name": physical_name,
            "domain_id": domain_id,
            "description": t.get("description"),
        }

        result = api_call(base_url, "POST", "/terms", payload)
        if result:
            created += 1
        else:
            errors += 1

    print(f"  Created: {created}, Skipped: {skipped}, Errors: {errors}")
    return created


def main():
    parser = argparse.ArgumentParser(
        description="반도체 제조 표준사전 시드 데이터 로딩",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python seed_semiconductor.py                            # localhost:4600
  python seed_semiconductor.py --base-url http://localhost:4600
  python seed_semiconductor.py --dry-run                  # 데이터 검증만
        """,
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:4600",
        help="Catalog Server base URL (default: http://localhost:4600)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API 호출 없이 데이터 검증만 수행",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Argus Catalog - Semiconductor Standard Dictionary Seeder")
    print("=" * 60)
    print(f"  Server:  {args.base_url}")
    print(f"  Dry-run: {args.dry_run}")
    print(f"  Data:    {SEED_DIR}")

    start = time.time()

    # 1. Dictionary
    dict_id = seed_dictionary(args.base_url, args.dry_run)
    if dict_id is None:
        print("\nFATAL: Failed to create dictionary. Aborting.")
        sys.exit(1)

    # 2. Words
    word_map = seed_words(args.base_url, dict_id, args.dry_run)

    # 3. Domains
    domain_map = seed_domains(args.base_url, dict_id, args.dry_run)

    # 4. Code Groups + Values
    seed_code_groups(args.base_url, dict_id, args.dry_run)

    # 5. Terms
    seed_terms(args.base_url, dict_id, word_map, domain_map, args.dry_run)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  Completed in {elapsed:.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
