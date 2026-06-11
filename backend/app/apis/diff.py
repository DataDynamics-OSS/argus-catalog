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

from typing import Any


def _op_key(ep: dict[str, Any]) -> str:
    return f"{str(ep.get('method', '')).upper()} {ep.get('path', '')}"


def _params_map(op: dict[str, Any]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for p in op.get("parameters") or []:
        if isinstance(p, dict):
            out[(p.get("name"), p.get("in"))] = p
    return out


def _diff_op(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
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
