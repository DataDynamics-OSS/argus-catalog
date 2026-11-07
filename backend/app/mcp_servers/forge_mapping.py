# SPDX-License-Identifier: Apache-2.0
"""Argus Forge(argus-agent-forge-internal) MCP 서버 매핑.

Forge 서버는 변경 도구를 ``allow.<그룹>`` 설정이 켜졌을 때만 등록하고, 리소스 범위를
서버별 fnmatch 설정 키로 제한한다. 이 모듈은 그 구조를 카탈로그의 정규화 모델
(도구 그룹 / 리소스 범위 규칙 / 대상 시스템·실행 계정)로 옮기는 정적 매핑이다.

출처: 각 서버의 ``platforms/<name>/mcp/config/config.example.yaml`` 주석(그룹별 도구 목록)과
설정 키. Forge 가 manifest 엔드포인트(design §6.2)를 제공하면 이 매핑은 시드·보조 용도로만 쓴다.

도구 하나가 여러 그룹으로 게이트되는 경우(예: impala/hive ``execute`` 는 dml·ddl·destructive
가 문장 종류별로 게이트) 카탈로그에서는 대표 그룹 하나에 두고 설명에 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ForgeGroup:
    """Forge 서버의 변경 도구 그룹 — ``config_key`` 설정이 켜져야 ``tools`` 가 등록된다.

    ``risk`` 는 low/medium/high/critical, ``default_enabled`` 는 예시 설정의 기본값.
    """

    key: str
    config_key: str
    label: str
    risk: str
    tools: tuple[str, ...] = ()
    mutating: bool = True
    default_enabled: bool = False
    description: str = ""


@dataclass(frozen=True)
class ForgeScope:
    """Forge 서버의 fnmatch 설정 키 하나를 카탈로그 리소스 범위 규칙(유형·동작·효과)으로 옮긴 것.

    ``effect``: allow(허용 목록) / deny(조회 금지) / protect(변경 금지) / mask(값 가림).
    """

    config_key: str
    resource_type: str
    action: str
    effect: str
    description: str = ""


@dataclass(frozen=True)
class ForgeServer:
    """Forge MCP 서버 한 종의 정적 정의 — 그룹·범위 규칙·설정 키 후보·도구별 리소스 유형."""

    name: str
    display_name: str
    target_platform: str
    port: int
    env_prefix: str
    description: str
    groups: tuple[ForgeGroup, ...] = ()
    scopes: tuple[ForgeScope, ...] = ()
    # 설정 경로 후보 (앞에서부터 첫 값). "a.b[].c" 는 목록 항목의 c
    target_keys: tuple[str, ...] = ()
    principal_keys: tuple[str, ...] = ()
    auth_keys: tuple[str, ...] = ()
    # 도구 이름 → 인자에서 꺼낼 리소스 유형(감사 이벤트 정책 판정용)
    resource_types: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def source_path(self) -> str:
        """Forge 저장소 안의 서버 소스 경로."""
        return f"platforms/{self.name}/mcp"

    @property
    def package_name(self) -> str:
        """배포 패키지 이름(``<name>-mcp``) — 카탈로그 서버 정의 이름으로도 쓴다."""
        return f"{self.name}-mcp"


FORGE_REPO = "argus-agent-forge-internal"

# 그룹 key "read" 는 모든 Forge 서버에 암묵적으로 존재(변경하지 않는 도구, 항상 등록).
READ_GROUP = ForgeGroup(
    key="read",
    config_key="",
    label="조회",
    risk="low",
    mutating=False,
    default_enabled=True,
    description="변경하지 않는 조회 도구 (항상 등록)",
)

FORGE_SERVERS: tuple[ForgeServer, ...] = (
    ForgeServer(
        name="nifi",
        display_name="NiFi MCP",
        target_platform="nifi",
        port=8788,
        env_prefix="NFA_",
        description="Apache NiFi 2.x REST API 를 다루는 MCP 서버",
        groups=(
            ForgeGroup(
                "write", "nifi.allow_write", "쓰기", "high", default_enabled=True,
                description="만들기·변경·삭제·시작·중지·파일 내보내기 (예시 설정 기본값 true)",
            ),
        ),
        target_keys=("nifi.url",),
        principal_keys=("nifi.username",),
    ),
    ForgeServer(
        name="prometheus",
        display_name="Prometheus MCP",
        target_platform="prometheus",
        port=8789,
        env_prefix="PROMETHEUS_MCP_",
        description="Prometheus HTTP API (Thanos/Mimir 호환) 질의 MCP 서버",
        groups=(
            ForgeGroup(
                "admin", "prometheus.admin", "관리 API", "critical",
                ("delete_series", "clean_tombstones", "snapshot"),
                description="Prometheus --web.enable-admin-api 필요",
            ),
        ),
        target_keys=("prometheus.url",),
        principal_keys=("prometheus.username",),
    ),
    ForgeServer(
        name="impala",
        display_name="Impala MCP",
        target_platform="impala",
        port=8791,
        env_prefix="IMPALA_MCP_",
        description="Apache Impala 4.x / CDP (HiveServer2 프로토콜) MCP 서버",
        groups=(
            ForgeGroup("refresh", "impala.allow.refresh", "메타데이터 갱신", "medium",
                       ("refresh_table", "invalidate_metadata")),
            ForgeGroup("compute_stats", "impala.allow.compute_stats", "통계 계산", "medium",
                       ("compute_stats",)),
            ForgeGroup("dml", "impala.allow.dml", "DML", "high", ("execute",),
                       description="execute: INSERT/UPSERT/UPDATE/DELETE(WHERE). ddl·destructive 는 같은 도구의 문장 종류로 게이트"),
            ForgeGroup("ddl", "impala.allow.ddl", "DDL", "high",
                       description="execute: CREATE / ALTER"),
            ForgeGroup("destructive", "impala.allow.destructive", "파괴적 작업", "critical",
                       description="execute: DROP / TRUNCATE / WHERE 없는 DELETE (ddl 과 함께)"),
            ForgeGroup("cancel", "daemons.allow_cancel", "전역 쿼리 취소", "medium",
                       ("cancel_query_global",)),
        ),
        target_keys=("impala.host",),
        principal_keys=("impala.doas_user", "impala.kerberos.principal", "impala.username"),
        auth_keys=("impala.auth",),
        resource_types={"execute": ("table",), "refresh_table": ("table",), "compute_stats": ("table",)},
    ),
    ForgeServer(
        name="hive",
        display_name="Hive MCP",
        target_platform="hive",
        port=8792,
        env_prefix="HIVE_MCP_",
        description="Apache Hive 3.1/4.x — Hive Metastore(Thrift) + HiveServer2(SQL) MCP 서버",
        groups=(
            ForgeGroup("partitions", "allow.partitions", "파티션", "medium",
                       ("add_partitions",), description="drop_partitions 는 destructive 도 필요"),
            ForgeGroup("properties", "allow.properties", "테이블 속성", "medium",
                       ("alter_table_properties",)),
            ForgeGroup("acid", "allow.acid", "ACID 관리", "high",
                       ("request_compaction", "abort_transaction")),
            ForgeGroup("dml", "allow.dml", "DML", "high", ("execute",),
                       description="execute: INSERT/UPDATE/DELETE(WHERE)/MERGE. ddl·destructive 는 문장 종류로 게이트"),
            ForgeGroup("ddl", "allow.ddl", "DDL", "high", description="execute: CREATE / ALTER"),
            ForgeGroup("maintenance", "allow.maintenance", "유지보수", "medium",
                       ("analyze_table", "repair_partitions")),
            ForgeGroup("destructive", "allow.destructive", "파괴적 작업", "critical",
                       ("drop_partitions",), description="execute 의 DROP/TRUNCATE, MSCK … DROP/SYNC 포함"),
        ),
        target_keys=("hiveserver2.host", "metastore.uris"),
        principal_keys=("hiveserver2.doas_user", "metastore.username", "kerberos.principal",
                        "hiveserver2.username"),
        auth_keys=("hiveserver2.auth", "metastore.auth"),
        resource_types={"execute": ("table",), "add_partitions": ("table",),
                        "drop_partitions": ("table",), "alter_table_properties": ("table",)},
    ),
    ForgeServer(
        name="atlas",
        display_name="Atlas MCP",
        target_platform="atlas",
        port=8796,
        env_prefix="ATLAS_MCP_",
        description="Apache Atlas 2.x — 검색·엔티티·리니지·영향 분석 MCP 서버",
        groups=(
            ForgeGroup("write", "atlas.allow.write", "쓰기", "medium",
                       ("add_classification", "remove_classification", "update_labels",
                        "update_attributes", "register_lineage")),
            ForgeGroup("delete", "atlas.allow.delete", "삭제", "high", ("delete_entity",)),
            ForgeGroup("types", "atlas.allow.types", "타입 정의", "high", ("apply_types",)),
        ),
        scopes=(
            ForgeScope("atlas.policy.writable_types", "entity_type", "write", "allow"),
            ForgeScope("atlas.policy.protected_types", "entity_type", "write", "protect"),
            ForgeScope("atlas.read.redact_attributes", "attribute", "read", "mask",
                       "이름에 이 단어가 든 속성 값을 가림"),
        ),
        target_keys=("atlas.url",),
        principal_keys=("atlas.principal", "atlas.username"),
        auth_keys=("atlas.auth",),
    ),
    ForgeServer(
        name="kafka",
        display_name="Kafka MCP",
        target_platform="kafka",
        port=8794,
        env_prefix="KAFKA_MCP_",
        description="Apache Kafka 2.8+/3.x — 토픽·컨슈머 그룹·Schema Registry·Connect MCP 서버",
        groups=(
            ForgeGroup("peek", "kafka.peek.enabled", "메시지 조회", "medium", ("peek_messages",),
                       mutating=False, default_enabled=True,
                       description="토픽 메시지 내용을 읽음 (deny_topics·redact_fields 적용)"),
            ForgeGroup("topics", "kafka.allow.topics", "토픽 관리", "high",
                       ("create_topic", "alter_topic_config", "add_partitions")),
            ForgeGroup("offsets", "kafka.allow.offsets", "오프셋 재설정", "high", ("reset_offsets",)),
            ForgeGroup("destructive", "kafka.allow.destructive", "파괴적 작업", "critical",
                       ("delete_topic", "delete_group")),
            ForgeGroup("connect_control", "connect.allow.control", "Connect 제어", "medium",
                       ("restart_connector", "set_connector_state")),
        ),
        scopes=(
            ForgeScope("kafka.peek.deny_topics", "topic", "read", "deny", "메시지 조회 금지 토픽"),
            ForgeScope("kafka.peek.redact_fields", "message_field", "read", "mask", "메시지 값에서 가릴 키"),
            ForgeScope("kafka.policy.protected_topics", "topic", "write", "protect", "변경·삭제 금지"),
            ForgeScope("kafka.policy.mutable_topics", "topic", "write", "allow", "변경 도구 대상 토픽"),
            ForgeScope("kafka.policy.protected_configs", "topic_config", "write", "protect"),
        ),
        target_keys=("kafka.bootstrap_servers",),
        principal_keys=("kafka.sasl.username", "kafka.kerberos.principal"),
        auth_keys=("kafka.security_protocol", "kafka.sasl.mechanism"),
        resource_types={
            "peek_messages": ("topic",), "create_topic": ("topic",), "alter_topic_config": ("topic",),
            "add_partitions": ("topic",), "delete_topic": ("topic",), "describe_topic": ("topic",),
            "reset_offsets": ("consumer_group", "topic"), "delete_group": ("consumer_group",),
        },
    ),
    ForgeServer(
        name="zookeeper",
        display_name="ZooKeeper MCP",
        target_platform="zookeeper",
        port=8795,
        env_prefix="ZOOKEEPER_MCP_",
        description="Apache ZooKeeper 3.5+ — 4lw·AdminServer·znode MCP 서버",
        groups=(
            ForgeGroup("write", "zookeeper.allow.write", "쓰기", "high", ("create_node", "set_node")),
            ForgeGroup("delete", "zookeeper.allow.delete", "삭제", "critical", ("delete_node",)),
        ),
        scopes=(
            ForgeScope("zookeeper.read.deny_paths", "znode_path", "read", "deny", "값을 읽지 않는 경로"),
            ForgeScope("zookeeper.policy.protected_paths", "znode_path", "write", "protect"),
            ForgeScope("zookeeper.policy.writable_paths", "znode_path", "write", "allow"),
        ),
        target_keys=("zookeeper.servers[].client",),
        principal_keys=("zookeeper.auth.username", "zookeeper.auth.sasl.principal"),
        auth_keys=("zookeeper.auth.mode",),
        resource_types={"create_node": ("znode_path",), "set_node": ("znode_path",),
                        "delete_node": ("znode_path",), "get_node": ("znode_path",)},
    ),
    ForgeServer(
        name="minio-aistor",
        display_name="MinIO AIStor MCP",
        target_platform="minio",
        port=8797,
        env_prefix="MINIO_MCP_",
        description="MinIO AIStor — S3·관리·KMS·Tables MCP 서버 (패키지 minio-mcp)",
        groups=(
            ForgeGroup("write", "minio.allow.write", "쓰기", "high",
                       ("create_bucket", "create_folder", "put_text_object", "copy_path")),
            ForgeGroup("delete", "minio.allow.delete", "삭제", "critical",
                       ("delete_paths", "delete_bucket", "move_path"),
                       description="move_path 는 write 와 함께일 때"),
            ForgeGroup("presign", "minio.allow.presign", "Presigned URL", "medium",
                       ("presign_download",), mutating=False,
                       description="링크를 가진 누구나 읽을 수 있음"),
            ForgeGroup("admin", "minio.allow.admin", "관리 API", "critical", ("run_operation",)),
        ),
        scopes=(
            ForgeScope("minio.policy.readable_buckets", "bucket", "read", "allow"),
            ForgeScope("minio.policy.writable_buckets", "bucket", "write", "allow"),
            ForgeScope("minio.policy.protected_buckets", "bucket", "write", "protect", "writable 보다 우선"),
            ForgeScope("minio.read.redact_keys", "config_key", "read", "mask"),
        ),
        target_keys=("minio.url",),
        resource_types={
            "create_bucket": ("bucket",), "create_folder": ("bucket",), "put_text_object": ("bucket",),
            "copy_path": ("bucket",), "delete_paths": ("bucket",), "delete_bucket": ("bucket",),
            "move_path": ("bucket",), "read_object": ("bucket",), "presign_download": ("bucket",),
        },
    ),
    ForgeServer(
        name="airflow",
        display_name="Airflow MCP",
        target_platform="airflow",
        port=8798,
        env_prefix="AIRFLOW_MCP_",
        description="Apache Airflow 3.x REST API v2 — DAG·실행·태스크·로그 MCP 서버",
        groups=(
            ForgeGroup("write", "airflow.allow.write", "쓰기", "high",
                       ("set_dag_paused", "trigger_dag_run", "clear_dag_run", "clear_task_instances",
                        "set_dag_run_state", "set_task_instance_state", "set_pool_slots")),
            ForgeGroup("delete", "airflow.allow.delete", "삭제", "critical", ("delete_dag_run",)),
        ),
        scopes=(
            ForgeScope("airflow.policy.writable_dags", "dag", "write", "allow"),
            ForgeScope("airflow.policy.protected_dags", "dag", "write", "protect", "writable_dags 보다 우선"),
        ),
        target_keys=("airflow.url",),
        principal_keys=("airflow.username",),
        auth_keys=("airflow.auth",),
        resource_types={"set_dag_paused": ("dag",), "trigger_dag_run": ("dag",), "clear_dag_run": ("dag",),
                        "delete_dag_run": ("dag",)},
    ),
    ForgeServer(
        name="ranger",
        display_name="Ranger MCP",
        target_platform="ranger",
        port=8799,
        env_prefix="RANGER_MCP_",
        description="Apache Ranger 2.x Admin REST — 서비스·정책·감사 MCP 서버",
        groups=(
            ForgeGroup("policies", "ranger.allow.policies", "정책 변경", "high",
                       ("create_policy", "update_policy", "set_policy_enabled", "remove_policy_principals")),
            ForgeGroup("delete", "ranger.allow.delete", "정책 삭제", "critical", ("delete_policy",)),
        ),
        scopes=(
            ForgeScope("ranger.policy.writable_services", "ranger_service", "write", "allow",
                       "비어 있으면 allow 를 켜도 모두 거부"),
            ForgeScope("ranger.policy.protected_services", "ranger_service", "write", "protect"),
        ),
        target_keys=("ranger.urls",),
        principal_keys=("ranger.username",),
        resource_types={"create_policy": ("ranger_service",), "update_policy": ("ranger_service",),
                        "delete_policy": ("ranger_service",)},
    ),
)

FORGE_BY_NAME = {s.name: s for s in FORGE_SERVERS}
# 패키지명(nifi-mcp, minio-mcp)·카탈로그 이름으로도 찾을 수 있게
FORGE_ALIASES = {
    **{s.package_name: s for s in FORGE_SERVERS},
    "minio-mcp": FORGE_BY_NAME["minio-aistor"],
    "minio": FORGE_BY_NAME["minio-aistor"],
}


def find_forge_server(name: str | None) -> ForgeServer | None:
    """Forge 서버 이름·패키지명·별칭(대소문자·앞뒤 공백 무시)으로 정의를 찾는다. 없으면 None."""
    if not name:
        return None
    key = name.strip().lower()
    return FORGE_BY_NAME.get(key) or FORGE_ALIASES.get(key)


def catalog_name(server: ForgeServer) -> str:
    """카탈로그 서버 정의 이름 (예: ``hive-mcp``)."""
    return server.package_name
