# SPDX-License-Identifier: Apache-2.0
"""페더레이션 서비스 파사드 — 기능별 모듈을 한곳에서 재노출한다.

구현은 registry(레지스트리·탐색) / drilldown(드릴다운·리니지) / importer(로컬 승격) /
export_service(노출자 export) / search(통합 검색) 모듈에 있다. 라우터·하베스터·테스트는
기존처럼 ``service.<이름>`` 으로 접근한다. 함수 교체(monkeypatch)는 정의 모듈에 해야 한다.
"""

from app.federation.drilldown import (  # noqa: F401
    _LINEAGE_IN_CHUNK,
    SampleNotDisplayedError,
    _call_peer_with_breaker,
    _record_peer_error,
    _resolve_lineage_nodes,
    build_lineage_graph,
    federated_dataset_detail,
    federated_dataset_sample,
)
from app.federation.export_service import (  # noqa: F401
    _EXPOSED_CATEGORY,
    _EXPOSED_KEY,
    _EXTENDED_FIELDS,
    _fetch_ddl,
    _fetch_extended,
    _fetch_glossary,
    _inject_schema_display_names,
    augment_and_filter_detail,
    build_export_datasets,
    build_export_lineage,
    get_effective_exposed_fields,
    get_exposed_fields,
    set_exposed_fields,
)
from app.federation.importer import (  # noqa: F401
    _EXT_DT_FIELDS,
    _EXT_INT_FIELDS,
    _EXT_STR_FIELDS,
    _IMPORT_DS_NAME,
    _IMPORT_DS_TYPE,
    _IMPORT_MARKER_TAG,
    _IMPORT_MARKER_TAG_COLOR,
    AlreadyImportedError,
    _apply_extended,
    _clip,
    _copy_sample_to_local,
    _get_or_create_import_datasource,
    _get_or_create_marker_tag_id,
    _match_glossary_id,
    _match_tag_id,
    _safe_dataset_name,
    import_federated_dataset,
)
from app.federation.registry import (  # noqa: F401
    _BROWSE_CAP,
    _BROWSE_UNGROUPED,
    _MIRROR_MODES,
    _escape_like,
    _parse_display_fields,
    _serialize_display_fields,
    _to_response,
    browse_instance_datasets,
    create_instance,
    delete_instance,
    federation_stats,
    get_instance,
    get_instance_by_key,
    list_active_peers,
    list_instances,
    resolve_federated_urn,
    update_instance,
)
from app.federation.search import (  # noqa: F401
    _local_hits,
    _mirror_keyword_ids,
    _mirror_semantic,
    _peer_hits,
    federated_search,
    search_mirror,
)
