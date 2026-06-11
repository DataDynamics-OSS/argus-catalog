# SPDX-License-Identifier: Apache-2.0
"""다운로드/게시 지표 차트를 채우기 위한 테스트 데이터 시드 스크립트.

MLflow 모델 대시보드와 OCI 모델 허브 대시보드의 차트는 모두 ``downloaded_at``
또는 ``finished_at`` / ``created_at`` 컬럼을 기준으로 최근 N일/24시간을 집계한다.
기존 데이터가 모두 30일보다 이전이라면 차트가 비어 보이는데, 이 스크립트는 그
공백을 채우기 위해 다음을 수행한다:

1. 등록된 MLflow / OCI 모델별 버전을 무작위로 선택해 download log 행을 삽입.
   timestamp 는 ``--days`` 일 전부터 현재까지에 가중치 분포(최근 24시간에 일부
   집중)로 흩뿌린다.
2. MLflow ``ModelVersion`` 일부와 OCI ``OciModelVersion`` 일부의
   ``finished_at`` / ``created_at`` 을 최근 30일 범위로 업데이트해서 게시(publish)
   차트도 함께 채운다.

실행:
    # 서버 디렉터리에서
    .venv/bin/python scripts/seed_metrics_test_data.py

옵션:
    --days N             과거 N일 범위(기본 30)
    --download-events N  추가할 download log 행 수(기본 250)
    --publish-touch N    finished_at/created_at 업데이트할 최대 버전 수(기본 25)
    --dry-run            DB 변경 없이 어떤 행이 추가/변경될지만 출력
"""

import argparse
import asyncio
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import engine
from app.models.models import ModelDownloadLog, ModelVersion, RegisteredModel
from app.oci_hub.models import OciModel, OciModelDownloadLog, OciModelVersion


# 시간 분포: 최근 24시간 35%, 7일 이내 추가 25%, 나머지 40% 는 N일 전체에 균등
def _random_timestamp(days_back: int, now: datetime) -> datetime:
    r = random.random()
    if r < 0.35:
        # 최근 24시간 내
        offset = timedelta(minutes=random.randint(0, 24 * 60))
    elif r < 0.60:
        # 최근 24시간 ~ 7일
        offset = timedelta(minutes=random.randint(24 * 60, 7 * 24 * 60))
    else:
        # 최근 7일 ~ N일
        offset = timedelta(minutes=random.randint(7 * 24 * 60, days_back * 24 * 60))
    return now - offset


async def _seed_mlflow_downloads(
    session: AsyncSession, count: int, days_back: int, dry_run: bool,
) -> int:
    # 모든 MLflow ModelVersion 행을 가져와 무작위 선택 풀로 사용
    versions = (await session.execute(
        select(ModelVersion.model_id, ModelVersion.version, RegisteredModel.name)
        .join(RegisteredModel, ModelVersion.model_id == RegisteredModel.id)
    )).all()
    if not versions:
        print("[MLflow] 등록된 ModelVersion 이 없습니다. download log 시드 건너뜀.")
        return 0

    now = datetime.now(timezone.utc)
    types = ["mlflow", "store"]
    inserted = 0
    for _ in range(count):
        _, version, model_name = random.choice(versions)
        ts = _random_timestamp(days_back, now)
        entry = ModelDownloadLog(
            model_name=model_name,
            version=version,
            download_type=random.choice(types),
            client_ip=f"10.0.{random.randint(0, 5)}.{random.randint(1, 254)}",
            user_agent="seed-test/1.0",
        )
        # downloaded_at 컬럼에 명시 timestamp 부여 (기본은 DB now())
        entry.downloaded_at = ts
        if not dry_run:
            session.add(entry)
        inserted += 1
    return inserted


async def _seed_oci_downloads(
    session: AsyncSession, count: int, days_back: int, dry_run: bool,
) -> int:
    models = (await session.execute(select(OciModel.id, OciModel.name))).all()
    if not models:
        print("[OCI] 등록된 OciModel 이 없습니다. download log 시드 건너뜀.")
        return 0

    now = datetime.now(timezone.utc)
    types = ["sdk", "cli", "rest"]
    inserted = 0
    for _ in range(count):
        _, model_name = random.choice(models)
        ts = _random_timestamp(days_back, now)
        entry = OciModelDownloadLog(
            model_name=model_name,
            version=random.randint(1, 3),
            download_type=random.choice(types),
            client_ip=f"10.0.{random.randint(0, 5)}.{random.randint(1, 254)}",
            user_agent="seed-test/1.0",
        )
        entry.downloaded_at = ts
        if not dry_run:
            session.add(entry)
        inserted += 1
    return inserted


async def _touch_mlflow_publish(
    session: AsyncSession, max_count: int, days_back: int, dry_run: bool,
) -> int:
    # READY 상태이며 finished_at 가 있는 row 중 N개를 골라 최근 시각으로 갱신
    candidates = (await session.execute(
        select(ModelVersion).where(ModelVersion.status == "READY").limit(max_count)
    )).scalars().all()

    if not candidates:
        print("[MLflow] READY 상태 버전이 없습니다. publish 시드 건너뜀.")
        return 0

    now = datetime.now(timezone.utc)
    touched = 0
    for mv in candidates:
        new_ts = _random_timestamp(days_back, now)
        if not dry_run:
            mv.finished_at = new_ts
        touched += 1
    return touched


async def _touch_oci_publish(
    session: AsyncSession, max_count: int, days_back: int, dry_run: bool,
) -> int:
    candidates = (await session.execute(
        select(OciModelVersion).limit(max_count)
    )).scalars().all()
    if not candidates:
        print("[OCI] OciModelVersion 이 없습니다. publish 시드 건너뜀.")
        return 0

    now = datetime.now(timezone.utc)
    touched = 0
    for v in candidates:
        new_ts = _random_timestamp(days_back, now)
        if not dry_run:
            v.created_at = new_ts
        touched += 1
    return touched


async def main(args: argparse.Namespace) -> None:
    random.seed(args.seed)

    async with AsyncSession(engine) as session:
        mlflow_downloads = await _seed_mlflow_downloads(
            session, args.download_events, args.days, args.dry_run,
        )
        oci_downloads = await _seed_oci_downloads(
            session, args.download_events // 3, args.days, args.dry_run,
        )
        mlflow_pubs = await _touch_mlflow_publish(
            session, args.publish_touch, args.days, args.dry_run,
        )
        oci_pubs = await _touch_oci_publish(
            session, args.publish_touch // 2, args.days, args.dry_run,
        )

        if args.dry_run:
            await session.rollback()
            print("[DRY-RUN] DB 변경 없음. 적용 시 결과 미리보기:")
        else:
            await session.commit()

        print(f"  - MLflow download log 추가: {mlflow_downloads}")
        print(f"  - OCI download log 추가: {oci_downloads}")
        print(f"  - MLflow ModelVersion.finished_at 갱신: {mlflow_pubs}")
        print(f"  - OCI OciModelVersion.created_at 갱신: {oci_pubs}")
        print(f"  - 범위: 최근 {args.days}일 (24h 35% / 7d 25% / {args.days}d 40%)")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="과거 N일 범위(기본 30)")
    parser.add_argument("--download-events", type=int, default=250, help="추가할 download log 행 수")
    parser.add_argument("--publish-touch", type=int, default=25, help="finished_at/created_at 업데이트할 최대 버전 수")
    parser.add_argument("--seed", type=int, default=42, help="random.seed (재현 가능)")
    parser.add_argument("--dry-run", action="store_true", help="DB 변경 없이 결과만 출력")
    asyncio.run(main(parser.parse_args()))
