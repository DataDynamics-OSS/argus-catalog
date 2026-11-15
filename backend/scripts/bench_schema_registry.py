# SPDX-License-Identifier: Apache-2.0
"""스키마 레지스트리 성능 측정 — 기존 외부 Avro API 와 같은 조건으로 비교한다 (TODO-052).

**시험용 서버에서만** 쓴다: 데이터 소스 하나와 데이터셋 N개(각 30컬럼)를 새로 만들고, 외부 API 캐시를 두 번 비운다.

사용:
    cd backend
    .venv/bin/python scripts/bench_schema_registry.py http://127.0.0.1:4600 --user admin --password admin --datasets 200

출력: 순차 요청(Bearer·Basic)의 p50·p95·최대, 동시 요청(기본 50)의 지연·처리량·오류 수.
관리자 계정으로 로그인해 데이터를 만들고, 측정용 API 키(범위 schema-registry:read)를 발급해 Basic 비밀번호 자리 키도 함께 잰다
(측정이 끝나면 그 키는 폐기).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import time

import httpx


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] * 1000


def _show(name: str, lat: list[float], errors: int = 0, wall: float | None = None) -> None:
    rps = f" rps={len(lat) / wall:.0f}" if wall else ""
    print(f"{name:<46} n={len(lat):<5} p50={_pct(lat, .5):6.1f}ms p95={_pct(lat, .95):6.1f}ms "
          f"max={max(lat) * 1000:6.1f}ms err={errors}{rps}")


async def _seq(client: httpx.AsyncClient, urls: list[str], headers: dict) -> list[float]:
    lat = []
    for u in urls:
        t = time.perf_counter()
        r = await client.get(u, headers=headers)
        lat.append(time.perf_counter() - t)
        if r.status_code != 200:
            raise SystemExit(f"실패: {u} → {r.status_code} {r.text[:200]}")
    return lat


async def _conc(client: httpx.AsyncClient, urls: list[str], headers: dict, concurrency: int):
    lat: list[float] = []
    errors = 0
    queue = list(urls)

    async def worker():
        nonlocal errors
        while queue:
            u = queue.pop()
            t = time.perf_counter()
            try:
                if (await client.get(u, headers=headers)).status_code != 200:
                    errors += 1
            except httpx.HTTPError:
                errors += 1
            lat.append(time.perf_counter() - t)

    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return lat, errors, time.perf_counter() - t0


async def main(args) -> None:
    api = f"{args.base.rstrip('/')}/api/v1"
    limits = httpx.Limits(max_connections=args.concurrency + 10)
    async with httpx.AsyncClient(timeout=60, limits=limits) as c:
        r = await c.post(f"{api}/auth/login", json={"username": args.user, "password": args.password})
        r.raise_for_status()
        bearer = {"Authorization": f"Bearer {r.json()['access_token']}"}
        basic = {"Authorization": "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()}
        r = await c.post(f"{api}/api-keys", headers=bearer,
                         json={"name": f"bench-{int(time.time())}", "scopes": ["schema-registry:read"], "expires_in_days": 1})
        r.raise_for_status()
        bench_key = r.json()
        basic_key = {"Authorization": "Basic " + base64.b64encode(f"bench:{bench_key['api_key']}".encode()).decode()}

        stamp = int(time.time())
        r = await c.post(f"{api}/catalog/datasources", headers=bearer,
                         json={"name": f"bench{stamp}", "type": "postgresql"})
        r.raise_for_status()
        ds = r.json()
        urns = []
        for i in range(args.datasets):
            fields = [{"field_path": f"col_{j}", "field_type": "STRING" if j % 3 else "NUMBER",
                       "native_type": "varchar(50)" if j % 3 else "bigint", "ordinal": j,
                       "description": f"컬럼 {j}"} for j in range(30)]
            r = await c.post(f"{api}/catalog/datasets", headers=bearer, json={
                "name": f"shop.t{i}", "datasource_id": ds["id"], "qualified_name": f"{ds['datasource_id']}.shop.t{i}",
                "description": f"테이블 {i}", "schema_fields": fields})
            r.raise_for_status()
            urns.append(r.json()["urn"])
        print(f"데이터셋 {args.datasets}개 × 30컬럼\n")

        ext = [f"{api}/external/avro-schema?urn={u}" for u in urns]
        sr = [f"{api}/schema-registry/subjects/{u}/versions/latest" for u in urns]
        await _seq(c, ext[:5], bearer)   # 워밍업

        print("== 순차 (Bearer)")
        _show("외부 Avro — 캐시 우회(no_cache)", await _seq(c, [u + "&no_cache=true" for u in ext], bearer))
        _show("외부 Avro — 캐시 미스(첫 호출)", await _seq(c, ext, bearer))
        _show("외부 Avro — 캐시 적중", await _seq(c, ext, bearer))
        _show("레지스트리 latest — 첫 호출(등록)", await _seq(c, sr, bearer))
        _show("레지스트리 latest — 캐시 적중", await _seq(c, sr, bearer))
        await c.delete(f"{api}/external/cache", headers=bearer)
        _show("레지스트리 latest — 캐시 미스(등록돼 있음)", await _seq(c, sr, bearer))
        ids = [(await c.get(u, headers=bearer)).json()["id"] for u in sr]
        by_id = [f"{api}/schema-registry/schemas/ids/{i}" for i in ids]
        _show("레지스트리 ids/{id} — 첫 호출", await _seq(c, by_id, bearer))
        _show("레지스트리 ids/{id} — 캐시 적중", await _seq(c, by_id, bearer))

        print("\n== 순차 (Basic — NiFi 방식)")
        _show("레지스트리 latest — Basic 첫 요청(비밀번호 검증)", await _seq(c, sr[:1], basic))
        _show("레지스트리 latest — Basic 캐시 적중", await _seq(c, sr, basic))
        _show("레지스트리 ids/{id} — Basic", await _seq(c, by_id, basic))
        _show("레지스트리 latest — Basic API 키", await _seq(c, sr, basic_key))

        n = args.concurrency
        print(f"\n== 동시 {n} (요청 {len(urns) * 10})")
        _show("외부 Avro — 캐시 적중, Bearer", *await _conc(c, ext * 10, bearer, n))
        _show("레지스트리 latest — 캐시 적중, Bearer", *await _conc(c, sr * 10, bearer, n))
        _show("레지스트리 latest — 캐시 적중, Basic", *await _conc(c, sr * 10, basic, n))
        _show("레지스트리 latest — 캐시 적중, Basic API 키", *await _conc(c, sr * 10, basic_key, n))
        _show("레지스트리 ids/{id} — Basic", *await _conc(c, by_id * 10, basic, n))
        await c.delete(f"{api}/external/cache", headers=bearer)
        _show(f"레지스트리 latest — 캐시 미스 {len(urns)}건, Basic", *await _conc(c, sr, basic, n))
        await c.delete(f"{api}/api-keys/{bench_key['id']}", headers=bearer)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("base", help="카탈로그 백엔드 주소 (예: http://127.0.0.1:4600)")
    p.add_argument("--user", default="admin")
    p.add_argument("--password", default="admin")
    p.add_argument("--datasets", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=50)
    asyncio.run(main(p.parse_args()))
