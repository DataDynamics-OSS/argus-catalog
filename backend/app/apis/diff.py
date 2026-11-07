# SPDX-License-Identifier: Apache-2.0
"""두 OpenAPI 스펙(엔드포인트) 간 변경 비교 + Breaking change 감지.

엔드포인트는 (METHOD, path) 로 매칭한다. Breaking 판정 규칙(MVP):
  - 엔드포인트 제거                      → Breaking
  - 필수 파라미터 추가                   → Breaking
  - 파라미터 선택→필수 변경              → Breaking
  - 필수 파라미터 제거                   → Breaking
  - 요청 바디 선택→필수(또는 필수로 추가) → Breaking
비파괴(non-breaking): 엔드포인트/선택 파라미터/응답 추가, 필수→선택 완화 등.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _op_key(ep: dict[str, Any]) -> str:
    """엔드포인트 매칭 키 ``"METHOD path"`` (메서드는 대문자)."""
    return f"{str(ep.get('method', '')).upper()} {ep.get('path', '')}"


def _params_map(op: dict[str, Any]) -> dict[tuple, dict]:
    """파라미터 목록 → ``{(name, in): 파라미터}`` — 같은 이름이라도 위치(in)가 다르면 다른 파라미터."""
    out: dict[tuple, dict] = {}
    for p in op.get("parameters") or []:
        if isinstance(p, dict):
            out[(p.get("name"), p.get("in"))] = p
    return out


def _diff_op(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """같은 (METHOD, path) 엔드포인트의 파라미터·요청 바디·응답 코드 변경 항목 ``{detail, breaking}`` 목록."""
    items: list[dict[str, Any]] = []
    oldp, newp = _params_map(old), _params_map(new)

    for key, p in oldp.items():
        if key not in newp:
            was_req = bool(p.get("required"))
            items.append({"detail": f"파라미터 제거: {key[0]} (in {key[1]})", "breaking": was_req})
    for key, p in newp.items():
        if key not in oldp:
            is_req = bool(p.get("required"))
            items.append({"detail": f"파라미터 추가: {key[0]} (in {key[1]})" + (" [필수]" if is_req else ""), "breaking": is_req})
        else:
            ow, nw = bool(oldp[key].get("required")), bool(p.get("required"))
            if not ow and nw:
                items.append({"detail": f"파라미터 필수화: {key[0]} (in {key[1]})", "breaking": True})
            elif ow and not nw:
                items.append({"detail": f"파라미터 선택화: {key[0]} (in {key[1]})", "breaking": False})

    ob = old.get("request_body") if isinstance(old.get("request_body"), dict) else None
    nb = new.get("request_body") if isinstance(new.get("request_body"), dict) else None
    obr = bool(ob.get("required")) if ob else False
    nbr = bool(nb.get("required")) if nb else False
    # 바디 추가는 새 바디가 필수일 때만, 제거는 기존 바디가 필수였을 때만 Breaking 으로 본다
    # (응답 코드 추가·제거는 MVP 에서 모두 비파괴로 취급)
    if not ob and nb:
        items.append({"detail": "요청 바디 추가", "breaking": nbr})
    elif ob and not nb:
        items.append({"detail": "요청 바디 제거", "breaking": obr})
    elif not obr and nbr:
        items.append({"detail": "요청 바디 필수화", "breaking": True})

    oresp = set((old.get("responses") or {}).keys())
    nresp = set((new.get("responses") or {}).keys())
    for code in sorted(oresp - nresp):
        items.append({"detail": f"응답 제거: {code}", "breaking": False})
    for code in sorted(nresp - oresp):
        items.append({"detail": f"응답 추가: {code}", "breaking": False})
    return items


def diff_endpoints(old_eps: list[dict], new_eps: list[dict]) -> dict[str, Any]:
    """두 엔드포인트 목록(파서 출력)을 비교한다.

    반환: ``added``/``removed`` (키 목록), ``changed`` (``{key, items, breaking}``), ``breaking``(bool),
    ``breaking_count`` (= 제거된 엔드포인트 수 + Breaking 변경 항목 수) 와 각 건수.
    """
    oldmap = {_op_key(e): e for e in old_eps}
    newmap = {_op_key(e): e for e in new_eps}

    added = sorted(k for k in newmap if k not in oldmap)
    removed = sorted(k for k in oldmap if k not in newmap)
    changed: list[dict[str, Any]] = []
    for k in sorted(oldmap.keys() & newmap.keys()):
        items = _diff_op(oldmap[k], newmap[k])
        if items:
            changed.append({"key": k, "items": items, "breaking": any(i["breaking"] for i in items)})

    breaking_count = len(removed) + sum(1 for c in changed for i in c["items"] if i["breaking"])
    logger.debug("스펙 비교 결과: added=%d removed=%d changed=%d breaking_count=%d",
                 len(added), len(removed), len(changed), breaking_count)
    return {
        "added": added,
        "removed": removed,           # breaking
        "changed": changed,
        "breaking": breaking_count > 0,
        "breaking_count": breaking_count,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
    }
