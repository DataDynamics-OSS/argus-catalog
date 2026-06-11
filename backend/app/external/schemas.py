# SPDX-License-Identifier: Apache-2.0
"""외부 메타데이터 API 용 Pydantic 스키마."""

from pydantic import BaseModel, Field


class ExternalDatasource(BaseModel):
    id: int
    datasource_id: str
    name: str
    type: str


class ExternalSchemaField(BaseModel):
    field_path: str
    field_type: str
    native_type: str | None = None
    description: str | None = None
    nullable: str = "true"
    is_primary_key: str = "false"
    is_unique: str = "false"
    is_indexed: str = "false"
    is_partition_key: str = "false"
    is_distribution_key: str = "false"
    ordinal: int = 0
    pii_type: str | None = None


class ExternalOwner(BaseModel):
    name: str
    type: str


class ExternalMetadata(BaseModel):
    """외부 시스템용 경량 메타데이터 JSON."""

    dataset_id: int
    urn: str
    name: str
    description: str | None = None
    origin: str
    status: str
    qualified_name: str | None = None
    table_type: str | None = None
    storage_format: str | None = None
    is_synced: str

    datasource: ExternalDatasource
    schema_fields: list[ExternalSchemaField] = Field(default_factory=list, alias="schema")
    tags: list[str] = Field(default_factory=list)
    owners: list[ExternalOwner] = Field(default_factory=list)
    properties: dict = Field(default_factory=dict)

    cache_info: dict | None = Field(None, alias="_cache")

    model_config = {"populate_by_name": True}


class CacheConfigUpdate(BaseModel):
    max_size: int = Field(ge=10, le=100000, description="Maximum cache entries")
    ttl_seconds: int = Field(ge=10, le=86400, description="TTL in seconds")
    enabled: bool = True


class CacheConfigResponse(BaseModel):
    max_size: int
    ttl_seconds: int
    enabled: bool
    current_size: int
