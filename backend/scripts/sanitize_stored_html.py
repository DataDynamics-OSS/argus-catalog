# SPDX-License-Identifier: Apache-2.0
"""이미 저장된 리치 텍스트 HTML 정화 (TODO-027).

저장 전 정화는 새로 쓰는 값에만 적용된다. 이 스크립트는 그 전에 저장된 댓글 본문·데이터셋 상세 설명·
변경 요청 설명을 같은 규칙(``app/core/html_sanitize.py``)으로 정화한다. 기본은 바뀔 행만 보여 주고(dry-run),
``--apply`` 를 줘야 저장한다. 태그 없는 일반 텍스트는 바뀌지 않는다.

사용:
    cd backend
    .venv/bin/python scripts/sanitize_stored_html.py            # 바뀔 행 수와 예시만 출력
    .venv/bin/python scripts/sanitize_stored_html.py --apply    # 저장
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.database import async_session  # noqa: E402
from app.core.html_sanitize import sanitize_rich_html  # noqa: E402

# (표, 키 컬럼, 본문 컬럼)
TARGETS = [
    ("catalog_comments", "id", "content"),
    ("catalog_datasets", "id", "description"),
    ("argus_change_request", "id", "description"),
]


async def run(apply: bool, show: int) -> int:
    total = 0
    async with async_session() as session:
        for table, key, col in TARGETS:
            rows = (await session.execute(text(
                f"SELECT {key}, {col} FROM {table} WHERE {col} LIKE '%<%'"))).fetchall()
            changed = [(r[0], r[1], sanitize_rich_html(r[1])) for r in rows]
            changed = [c for c in changed if c[2] != c[1]]
            print(f"{table}.{col}: 태그 포함 {len(rows)}행, 정화로 바뀌는 행 {len(changed)}")
            for rid, before, after in changed[:show]:
                print(f"  - {key}={rid}\n    전: {before[:200]!r}\n    후: {after[:200]!r}")
            if apply and changed:
                await session.execute(
                    text(f"UPDATE {table} SET {col} = :v WHERE {key} = :k"),
                    [{"v": after, "k": rid} for rid, _, after in changed])
            total += len(changed)
        if apply:
            await session.commit()
    print(("저장했습니다" if apply else "dry-run — 저장하지 않았습니다(--apply 로 저장)") + f": 총 {total}행")
    return total


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="정화 결과를 저장한다(기본은 미리보기)")
    p.add_argument("--show", type=int, default=3, help="표마다 보여 줄 예시 행 수")
    args = p.parse_args()
    asyncio.run(run(args.apply, args.show))


if __name__ == "__main__":
    main()
