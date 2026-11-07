# SPDX-License-Identifier: Apache-2.0
"""Scan model-artifacts directory and populate catalog_models table.

서버 데이터 디렉터리의 MLflow 모델 파일(MLmodel 등)을 훑어 catalog_models 행을 채우는 1회성 스크립트.

Usage:
    cd backend
    python scripts/seed_catalog_models.py

Reads every {model_name}/versions/{N}/ directory under data_dir/model-artifacts/,
parses MLmodel, requirements.txt, conda.yaml, python_env.yaml,
and inserts a row into catalog_models if one doesn't already exist.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import async_session, engine, init_database  # noqa: E402
from app.models.models import CatalogModel, ModelVersion, RegisteredModel  # noqa: E402
from app.models.service import _extract_mlmodel_fields, _read_file_text  # noqa: E402

import app.catalog.models  # noqa: E402, F401 — register ORM models
import app.models.models  # noqa: E402, F401
import app.usermgr.models  # noqa: E402, F401

from sqlalchemy import and_, select  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    """model-artifacts/{모델}/versions/{N}/ 를 순회해 없는 (모델, 버전) 만 삽입한다.

    MLmodel 파일이 없거나 대응하는 model_version 행이 없으면 건너뛴다.
    """
    await init_database()
    # 스키마는 SQL DDL(packaging/config/argus-catalog-*.sql)로 사전 생성돼 있어야 한다.

    artifacts_root = settings.data_dir / "model-artifacts"
    if not artifacts_root.is_dir():
        logger.error("model-artifacts directory not found: %s", artifacts_root)
        return

    inserted = 0
    skipped = 0

    async with async_session() as session:
        # Iterate: model-artifacts/{model_name}/versions/{version}/
        for model_dir in sorted(artifacts_root.iterdir()):
            if not model_dir.is_dir():
                continue
            model_name = model_dir.name

            versions_dir = model_dir / "versions"
            if not versions_dir.is_dir():
                continue

            for ver_dir in sorted(versions_dir.iterdir()):
                if not ver_dir.is_dir():
                    continue
                try:
                    version = int(ver_dir.name)
                except ValueError:
                    continue

                # Check if MLmodel exists
                if not (ver_dir / "MLmodel").is_file():
                    logger.warning("Skipping %s v%d: no MLmodel file", model_name, version)
                    continue

                # Check if already exists
                existing = await session.execute(
                    select(CatalogModel).where(
                        CatalogModel.model_name == model_name,
                        CatalogModel.version == version,
                    )
                )
                if existing.scalars().first():
                    logger.info("Already exists: %s v%d — skipping", model_name, version)
                    skipped += 1
                    continue

                # Resolve model_version_id from DB
                mv_result = await session.execute(
                    select(ModelVersion.id).join(
                        RegisteredModel,
                        RegisteredModel.id == ModelVersion.model_id,
                    ).where(
                        and_(
                            RegisteredModel.name == model_name,
                            ModelVersion.version == version,
                        )
                    )
                )
                mv_id = mv_result.scalar()
                if mv_id is None:
                    logger.warning(
                        "No model_version record for %s v%d — skipping",
                        model_name, version,
                    )
                    skipped += 1
                    continue

                # Read files
                requirements = _read_file_text(ver_dir / "requirements.txt")
                conda = _read_file_text(ver_dir / "conda.yaml")
                python_env = _read_file_text(ver_dir / "python_env.yaml")
                ml_fields = _extract_mlmodel_fields(ver_dir)

                catalog_model = CatalogModel(
                    model_version_id=mv_id,
                    model_name=model_name,
                    version=version,
                    requirements=requirements,
                    conda=conda,
                    python_env=python_env,
                    **ml_fields,
                )
                session.add(catalog_model)
                logger.info(
                    "Inserted: %s v%d (sklearn=%s, mlflow=%s, predict_fn=%s)",
                    model_name, version,
                    ml_fields.get("sklearn_version"),
                    ml_fields.get("mlflow_version"),
                    ml_fields.get("predict_fn"),
                )
                inserted += 1

        await session.commit()

    logger.info("Done: %d inserted, %d skipped", inserted, skipped)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
