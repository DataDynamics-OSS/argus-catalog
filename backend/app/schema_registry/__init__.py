# SPDX-License-Identifier: Apache-2.0
"""Confluent 호환 스키마 레지스트리 (읽기 전용).

subject = 데이터셋 URN, 본문 = 외부 Avro API 와 같은 Avro record(``app.external.service``).
id·버전은 Avro 본문 지문마다 한 행(``argus_schema_registry_version``)으로 남아 바뀌지 않는다.
"""
