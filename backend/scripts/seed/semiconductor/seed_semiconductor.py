#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""반도체 제조 표준사전 시드 데이터 로딩 스크립트.

Catalog Server Standard API를 호출하여 반도체 Fab 표준 단어/도메인/코드/용어를 일괄 등록한다.

표준 API 는 로그인이 필요하다(쓰기는 표준사전 편집 권한). 스크립트는 먼저
``POST /api/v1/auth/login`` 으로 토큰을 받아 모든 요청에 ``Authorization: Bearer`` 헤더를 붙인다.
계정은 SDK CLI 와 같은 관례를 따른다.

  - 사용자: ``--username`` > 환경변수 ``ARGUS_USERNAME`` > ``admin``
  - 비밀번호: ``--password`` > 환경변수 ``ARGUS_PASSWORD`` > (터미널이면) 입력 프롬프트
  - 이미 받은 토큰: ``--token`` 또는 환경변수 ``ARGUS_TOKEN`` (주면 로그인 생략)

Usage:
    ARGUS_PASSWORD=... python seed_semiconductor.py      # localhost:4600, admin 계정
    python seed_semiconductor.py --base-url http://localhost:4600 --username admin
    python seed_semiconductor.py --dry-run                # API 호출 없이 데이터 검증만(로그인 안 함)
"""

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SEED_DIR = Path(__file__).parent
API_PREFIX = "/api/v1/standards"
LOGIN_PATH = "/api/v1/auth/login"

# 로그인 후 모든 API 호출에 붙일 액세스 토큰(main 에서 설정)
_access_token: str | None = None


class SeedAuthError(Exception):
    """로그인 실패 — 메시지는 사용자에게 그대로 보여 준다."""


def _error_detail(body: str) -> tuple[str, str | None]:
    """오류 응답 본문에서 (detail, code) 를 뽑는다. JSON 이 아니면 본문 앞부분."""
    try:
        data = json.loads(body)
    except ValueError:
        return body[:300], None
    if isinstance(data, dict):
        return str(data.get("detail") or body[:300]), data.get("code")
    return body[:300], None


def login(base_url: str, username: str, password: str, timeout: float = 30) -> str:
    """``POST /api/v1/auth/login`` 으로 로그인해 액세스 토큰을 돌려준다.

    실패하면 ``SeedAuthError`` (사용자에게 보여 줄 한국어 메시지).
    """
    url = f"{base_url}{LOGIN_PATH}"
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail, _ = _error_detail(e.read().decode("utf-8", errors="replace"))
        if e.code == 401:
            raise SeedAuthError(f"로그인 실패: 사용자명 또는 비밀번호가 올바르지 않습니다 ({username}).") from e
        if e.code == 423:
            raise SeedAuthError(f"로그인 실패: 계정이 잠겼습니다 — {detail}") from e
        raise SeedAuthError(f"로그인 실패 (HTTP {e.code}): {detail}") from e
    except URLError as e:
        raise SeedAuthError(f"로그인 실패: 서버에 연결할 수 없습니다 ({url}): {e.reason}") from e

    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise SeedAuthError("로그인 실패: 응답에 access_token 이 없습니다.")
    if _token_requires_password_change(token):
        raise SeedAuthError(
            f"'{username}' 계정은 비밀번호를 먼저 변경해야 합니다(최초 로그인 강제 변경 대상). "
            "웹 UI 에 로그인해 비밀번호를 바꾼 뒤 새 비밀번호로 다시 실행하세요."
        )
    return token


def _token_requires_password_change(token: str) -> bool:
    """로컬 JWT 의 ``must_change_password`` claim 확인(서명 검증 없이 payload 만 읽음).

    Keycloak 토큰 등 claim 이 없거나 해석할 수 없으면 False — 실제 차단은 API 의 403 응답으로 다시 잡는다.
    """
    import base64

    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, ValueError):
        return False
    return bool(isinstance(payload, dict) and payload.get("must_change_password"))


def _fatal_auth(code: int, detail: str, error_code: str | None) -> None:
    """인증·권한 오류는 계속해도 모두 실패하므로 원인을 알리고 종료한다."""
    if code == 403 and error_code == "PASSWORD_CHANGE_REQUIRED":
        print("\nFATAL: 비밀번호를 먼저 변경해야 하는 계정입니다(강제 변경 게이트). "
              "웹 UI 에서 비밀번호를 바꾼 뒤 다시 실행하세요.")
    elif code == 401:
        print(f"\nFATAL: 인증이 거부되었습니다(401) — 토큰이 없거나 만료되었습니다: {detail}")
    else:
        print(f"\nFATAL: 권한이 없습니다(403) — 표준사전 편집 권한이 있는 계정으로 실행하세요: {detail}")
    sys.exit(1)


def load_json(filename: str) -> list | dict:
    """이 스크립트 옆의 시드 JSON 파일을 읽는다."""
    path = SEED_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def api_call(base_url: str, method: str, path: str, data: dict | None = None) -> dict | None:
    """표준 API 호출 — JSON 응답 반환, 204·중복(409/unique)·기타 HTTP 오류는 None.

    토큰이 있으면 ``Authorization: Bearer`` 헤더를 붙인다. 연결 실패와 인증/권한 오류(401/403)는
    이후 호출도 모두 실패하므로 즉시 종료한다.
    """
    url = f"{base_url}{API_PREFIX}{path}"
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None
    req = Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if _access_token:
        req.add_header("Authorization", f"Bearer {_access_token}")

    try:
        with urlopen(req, timeout=30) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403):
            detail, error_code = _error_detail(error_body)
            _fatal_auth(e.code, detail, error_code)
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
                # (dry-run) 단어 매핑 확인용 — word_map 값은 dry-run 에서 -1 이라 약어는 만들지 않는다
                abbrs = []
                for w_name in t["words"]:
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

    # 단어 영문명·약어 조회표 — words.json 은 한 번만 읽는다
    word_lookup = {w["word_name"]: w for w in load_json("words.json")}

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

        # words.json 의 영문명·약어로 용어 영문명/약어를 직접 조합한다(형태소 분석 API 는 쓰지 않음).
        # 사전에 없는 단어(X, Y 같은 한 글자·복합어)는 이름을 그대로 쓴다.
        word_names = t.get("words", [])
        term_english_parts = []
        term_abbr_parts = []
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


def authenticate(args: argparse.Namespace) -> None:
    """토큰을 정해 ``_access_token`` 에 둔다. 실패하면 메시지를 찍고 종료."""
    global _access_token

    if args.token:
        _access_token = args.token
        print("  Auth:    --token / ARGUS_TOKEN 사용")
        return

    password = args.password
    if not password:
        if not sys.stdin.isatty():
            print("\nFATAL: 비밀번호가 없습니다. --password 또는 환경변수 ARGUS_PASSWORD 로 지정하세요.")
            sys.exit(1)
        password = getpass.getpass(f"  '{args.username}' 비밀번호: ")

    try:
        _access_token = login(args.base_url, args.username, password)
    except SeedAuthError as e:
        print(f"\nFATAL: {e}")
        sys.exit(1)
    print(f"  Auth:    {args.username} 로그인 성공")


def main():
    """사전 → 단어 → 도메인 → 코드그룹/코드값 → 용어 순서로 시드한다(사전 생성 실패 시 종료)."""
    parser = argparse.ArgumentParser(
        description="반도체 제조 표준사전 시드 데이터 로딩",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ARGUS_PASSWORD=... python seed_semiconductor.py         # localhost:4600, admin 계정
  python seed_semiconductor.py --base-url http://localhost:4600 --username admin
  python seed_semiconductor.py --dry-run                  # 데이터 검증만(로그인 안 함)

계정: --username / ARGUS_USERNAME (기본 admin), --password / ARGUS_PASSWORD
(없으면 터미널에서 입력), 또는 --token / ARGUS_TOKEN.
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
    parser.add_argument(
        "--username",
        default=os.environ.get("ARGUS_USERNAME", "admin"),
        help="로그인 사용자 (env ARGUS_USERNAME, 기본 admin)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("ARGUS_PASSWORD"),
        help="로그인 비밀번호 (env ARGUS_PASSWORD, 없으면 입력 프롬프트)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("ARGUS_TOKEN"),
        help="이미 받은 액세스 토큰 (env ARGUS_TOKEN, 주면 로그인 생략)",
    )
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")

    print("=" * 60)
    print("  Argus Catalog - Semiconductor Standard Dictionary Seeder")
    print("=" * 60)
    print(f"  Server:  {args.base_url}")
    print(f"  Dry-run: {args.dry_run}")
    print(f"  Data:    {SEED_DIR}")

    if not args.dry_run:
        authenticate(args)

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
