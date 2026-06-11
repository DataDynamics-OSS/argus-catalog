"use client"

import { useState } from "react"
import { ChevronRight, Settings2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@workspace/ui/components/card"

// ---------------------------------------------------------------------------
// Collapsible section
// ---------------------------------------------------------------------------

function CollapsibleSection({
  title,
  defaultOpen = false,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="mt-3">
      <button
        type="button"
        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => setOpen(!open)}
      >
        <ChevronRight
          className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`}
        />
        {title}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B"
  const units = ["B", "KB", "MB", "GB", "TB"]
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

// ---------------------------------------------------------------------------
// Table-level property renderers
// ---------------------------------------------------------------------------

type PropItem = { label: string; value: string }

function mysqlTableProps(table: Record<string, unknown>): PropItem[] {
  const items: PropItem[] = []
  if (table.engine) items.push({ label: "Engine", value: String(table.engine) })
  if (table.row_format) items.push({ label: "Row Format", value: String(table.row_format) })
  if (table.collation) items.push({ label: "Collation", value: String(table.collation) })
  if (table.estimated_rows != null)
    items.push({ label: "Estimated Rows", value: formatNumber(Number(table.estimated_rows)) })
  if (table.data_size != null)
    items.push({ label: "Data Size", value: formatBytes(Number(table.data_size)) })
  if (table.index_size != null)
    items.push({ label: "Index Size", value: formatBytes(Number(table.index_size)) })
  if (table.avg_row_length != null)
    items.push({ label: "Avg Row Length", value: `${formatNumber(Number(table.avg_row_length))} B` })
  if (table.auto_increment != null)
    items.push({ label: "Auto Increment", value: formatNumber(Number(table.auto_increment)) })
  if (table.create_time) items.push({ label: "Created", value: String(table.create_time) })
  if (table.update_time) items.push({ label: "Updated", value: String(table.update_time) })
  if (table.create_options) items.push({ label: "Options", value: String(table.create_options) })
  return items
}

function hiveImpalaTableProps(table: Record<string, unknown>): PropItem[] {
  const items: PropItem[] = []
  if (table.table_type) items.push({ label: "Table Type", value: String(table.table_type) })
  if (table.location) items.push({ label: "Location", value: String(table.location) })
  if (table.storage_format) items.push({ label: "Storage Format", value: String(table.storage_format) })
  if (table.compression) items.push({ label: "Compression", value: String(table.compression) })
  if (table.serde) items.push({ label: "SerDe", value: String(table.serde) })
  if (table.input_format) items.push({ label: "Input Format", value: String(table.input_format) })
  if (table.output_format) items.push({ label: "Output Format", value: String(table.output_format) })
  if (table.field_delimiter) items.push({ label: "Field Delimiter", value: String(table.field_delimiter) })
  if (table.line_delimiter) items.push({ label: "Line Delimiter", value: String(table.line_delimiter) })
  if (table.collection_delimiter)
    items.push({ label: "Collection Delimiter", value: String(table.collection_delimiter) })
  if (table.map_key_delimiter) items.push({ label: "Map Key Delimiter", value: String(table.map_key_delimiter) })
  if (table.partition_keys) items.push({ label: "Partition Keys", value: String(table.partition_keys) })
  if (table.bucket_columns) items.push({ label: "Bucket Columns", value: String(table.bucket_columns) })
  if (table.bucket_count != null)
    items.push({ label: "Bucket Count", value: formatNumber(Number(table.bucket_count)) })
  if (table.sort_columns) items.push({ label: "Sort Columns", value: String(table.sort_columns) })
  if (table.transactional != null)
    items.push({ label: "Transactional", value: table.transactional ? "Yes" : "No" })
  if (table.table_format) items.push({ label: "Table Format", value: String(table.table_format) })
  if (table.metadata_location) items.push({ label: "Metadata Location", value: String(table.metadata_location) })
  if (table.spark_provider) items.push({ label: "Spark Provider", value: String(table.spark_provider) })
  if (table.kudu_master_hosts) items.push({ label: "Kudu Masters", value: String(table.kudu_master_hosts) })
  if (table.estimated_rows != null)
    items.push({ label: "Estimated Rows", value: formatNumber(Number(table.estimated_rows)) })
  if (table.total_size != null)
    items.push({ label: "Total Size", value: formatBytes(Number(table.total_size)) })
  if (table.num_files != null)
    items.push({ label: "Number of Files", value: formatNumber(Number(table.num_files)) })
  if (table.owner) items.push({ label: "Owner", value: String(table.owner) })
  if (table.created_at) items.push({ label: "Created", value: String(table.created_at) })
  return items
}

function pgTableProps(table: Record<string, unknown>): PropItem[] {
  const items: PropItem[] = []
  if (table.owner) items.push({ label: "Owner", value: String(table.owner) })
  if (table.kind) items.push({ label: "Kind", value: String(table.kind) })
  if (table.persistence) items.push({ label: "Persistence", value: String(table.persistence) })
  if (table.estimated_rows != null)
    items.push({ label: "Estimated Rows", value: formatNumber(Number(table.estimated_rows)) })
  if (table.total_size != null)
    items.push({ label: "Total Size", value: formatBytes(Number(table.total_size)) })
  if (table.table_size != null)
    items.push({ label: "Table Size", value: formatBytes(Number(table.table_size)) })
  if (table.index_size != null)
    items.push({ label: "Index Size", value: formatBytes(Number(table.index_size)) })
  if (table.has_indexes != null)
    items.push({ label: "Has Indexes", value: table.has_indexes ? "Yes" : "No" })
  if (table.has_triggers != null)
    items.push({ label: "Has Triggers", value: table.has_triggers ? "Yes" : "No" })
  return items
}

// ---------------------------------------------------------------------------
// COLUMNS (HTML table) — TblPropertiesGrid 와 동일한 시각 패턴.
// ---------------------------------------------------------------------------

function ColumnsGrid({
  datasourceType,
  columns,
}: {
  datasourceType: string
  columns: Record<string, Record<string, unknown>>
}) {
  const entries = Object.entries(columns)
  if (entries.length === 0) return null

  const isMySQL = datasourceType === "mysql"
  // 헤더와 row 추출기를 한 곳에 두어 datasource 별 분기를 단순화.
  const headers = isMySQL
    ? ["Column", "Key", "Default", "Extra", "Charset", "Collation"]
    : ["Column", "Default", "Constraints"]
  const renderRow = (colName: string, props: Record<string, unknown>): string[] => {
    if (isMySQL) {
      return [
        colName,
        String(props.key ?? ""),
        String(props.default ?? ""),
        String(props.extra ?? ""),
        String(props.charset ?? ""),
        String(props.collation ?? ""),
      ]
    }
    const constraints = Array.isArray(props.constraints)
      ? (props.constraints as { type: string; references?: string }[])
          .map((c) => (c.references ? `${c.type} → ${c.references}` : c.type))
          .join(", ")
      : ""
    return [colName, String(props.default ?? ""), constraints]
  }

  return (
    <CollapsibleSection title={`COLUMNS (${entries.length})`}>
      <div className="rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-muted-foreground">
            <tr>
              {headers.map((h) => (
                <th key={h} className="px-3 py-1.5 text-left font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {entries.map(([colName, props]) => {
              const cells = renderRow(colName, props)
              return (
                <tr key={colName} className="border-t align-top">
                  {cells.map((v, i) => (
                    <td
                      key={i}
                      // 첫 컬럼(Column 명) 외 모든 cell 은 D2Coding 모노로 정렬 가독성 확보.
                      className={
                        "px-3 py-1.5 break-all " +
                        (i === 0 ? "" : "font-[family-name:var(--font-d2coding)]")
                      }
                    >
                      {v}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </CollapsibleSection>
  )
}

// ---------------------------------------------------------------------------
// INDEXES (HTML table)
// ---------------------------------------------------------------------------

function IndexesGrid({ indexes }: { indexes: { name: string; definition: string }[] }) {
  if (!indexes || indexes.length === 0) return null

  return (
    <CollapsibleSection title={`INDEXES (${indexes.length})`}>
      <div className="rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-muted-foreground">
            <tr>
              <th className="w-[220px] px-3 py-1.5 text-left font-medium">Name</th>
              <th className="px-3 py-1.5 text-left font-medium">Definition</th>
            </tr>
          </thead>
          <tbody>
            {indexes.map((ix) => (
              <tr key={ix.name} className="border-t align-top">
                <td className="px-3 py-1.5 break-all">{ix.name}</td>
                <td className="px-3 py-1.5 break-all font-[family-name:var(--font-d2coding)]">
                  {ix.definition}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CollapsibleSection>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

type DatasetPropertyItem = {
  id: number
  property_key: string
  property_value: string
}

type DatasourceSpecificCardProps = {
  datasourceType: string
  properties: Record<string, unknown>
  /** Dataset properties from catalog_dataset_properties (hive.* keys) */
  datasetProperties?: DatasetPropertyItem[]
  /** ``a.b.c`` qualified dataset name (네임스페이스 포함). Iceberg 사용 가이드용. */
  datasetName?: string
}

function TblPropertiesGrid({ tblProperties }: { tblProperties: Record<string, string> }) {
  const entries = Object.entries(tblProperties)
  if (entries.length === 0) return null

  return (
    <CollapsibleSection title={`TABLE PROPERTIES (${entries.length})`} defaultOpen>
      {/* 평범한 HTML 테이블 — 긴 value 도 줄바꿈으로 안전하게 표시. row 가 많으면
          ``max-h`` + overflow 로 스크롤. */}
      <div className="rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-muted-foreground">
            <tr>
              <th className="w-[220px] px-3 py-1.5 text-left font-medium">Key</th>
              <th className="px-3 py-1.5 text-left font-medium">Value</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, value]) => (
              <tr key={key} className="border-t align-top">
                <td className="px-3 py-1.5 break-all">{key}</td>
                {/* Value 만 D2Coding 모노. Key/헤더는 본문과 동일한 sans 글꼴. */}
                <td className="px-3 py-1.5 break-all font-[family-name:var(--font-d2coding)]">{String(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CollapsibleSection>
  )
}

export function DatasourceSpecificCard({ datasourceType, properties, datasetProperties, datasetName: _datasetName }: DatasourceSpecificCardProps) {
  const table = (properties.table ?? {}) as Record<string, unknown>
  const columns = (properties.columns ?? {}) as Record<string, Record<string, unknown>>
  const indexes = (properties.indexes ?? []) as { name: string; definition: string }[]
  // DDL 은 dataset.ddl 컬럼 + 별도 DDL tab 으로 통일했으므로 여기서는 다루지 않는다.
  let tblProperties = (properties.tbl_properties ?? {}) as Record<string, string>

  const isMySQL = datasourceType === "mysql"
  const isPostgres = datasourceType === "postgresql"
  const isHiveImpala = datasourceType === "hive" || datasourceType === "impala"
  const isIcebergRest = datasourceType === "iceberg-rest"

  // Iceberg REST Catalog 분기 — Hive/Impala 와 같은 패턴으로 모든 ``iceberg.*`` 키를
  // 한 그리드(Key/Value)로 통째 노출. 별도 상단 박스를 두지 않는다.
  if (isIcebergRest) {
    const icebergProps: Record<string, string> = {}
    for (const p of datasetProperties ?? []) {
      if (!p.property_key.startsWith("iceberg.")) continue
      // prefix(``iceberg.``) 를 제거한 키를 사용 — Hive 카드와 동일한 스타일.
      icebergProps[p.property_key.slice(8)] = p.property_value
    }
    if (Object.keys(icebergProps).length === 0) {
      return null
    }
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Settings2 className="h-4 w-4" />
            Datasource Specific (Iceberg)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <TblPropertiesGrid tblProperties={icebergProps} />
        </CardContent>
      </Card>
    )
  }

  // For Hive/Impala: merge hive.* dataset properties into table props and tbl_properties
  const mergedTable = { ...table }
  if (isHiveImpala && datasetProperties && datasetProperties.length > 0) {
    const dsProps: Record<string, string> = {}
    for (const p of datasetProperties) {
      const key = p.property_key.startsWith("hive.") ? p.property_key.slice(5) : p.property_key
      dsProps[key] = p.property_value
    }

    // Map known keys to hiveImpalaTableProps format
    if (dsProps.table_format && !mergedTable.table_format) mergedTable.table_format = dsProps.table_format
    if (dsProps.location && !mergedTable.location) mergedTable.location = dsProps.location
    if (dsProps.input_format && !mergedTable.input_format) mergedTable.input_format = dsProps.input_format
    if (dsProps.partition_keys && !mergedTable.partition_keys) mergedTable.partition_keys = dsProps.partition_keys
    if (dsProps.numRows && !mergedTable.estimated_rows) mergedTable.estimated_rows = dsProps.numRows
    if (dsProps.totalSize && !mergedTable.total_size) mergedTable.total_size = dsProps.totalSize
    if (dsProps.metadata_location && !mergedTable.metadata_location) mergedTable.metadata_location = dsProps.metadata_location
    if (dsProps["spark.sql.sources.provider"] && !mergedTable.spark_provider) mergedTable.spark_provider = dsProps["spark.sql.sources.provider"]

    // Build tbl_properties from remaining hive.* keys
    if (Object.keys(tblProperties).length === 0) {
      const merged: Record<string, string> = {}
      for (const p of datasetProperties) {
        merged[p.property_key] = p.property_value
      }
      tblProperties = merged
    }
  }

  // MySQL: sync 어댑터가 datasource_properties.table 을 채우지 않고 catalog_dataset_properties
  // 에 ``mysql.*`` key 로만 저장하므로 hive 패턴과 동일하게 머지해 mysqlTableProps 입력 형식
  // 으로 보강.
  if (isMySQL && datasetProperties && datasetProperties.length > 0) {
    const dsProps: Record<string, string> = {}
    for (const p of datasetProperties) {
      const key = p.property_key.startsWith("mysql.") ? p.property_key.slice(6) : p.property_key
      dsProps[key] = p.property_value
    }
    if (dsProps.engine && !mergedTable.engine) mergedTable.engine = dsProps.engine
    if (dsProps.row_format && !mergedTable.row_format) mergedTable.row_format = dsProps.row_format
    if (dsProps.collation && !mergedTable.collation) mergedTable.collation = dsProps.collation
    if (dsProps.table_rows && mergedTable.estimated_rows == null) mergedTable.estimated_rows = dsProps.table_rows
    if (dsProps.data_length && mergedTable.data_size == null) mergedTable.data_size = dsProps.data_length
    if (dsProps.index_length && mergedTable.index_size == null) mergedTable.index_size = dsProps.index_length
    if (dsProps.avg_row_length && mergedTable.avg_row_length == null) mergedTable.avg_row_length = dsProps.avg_row_length
    if (dsProps.auto_increment && mergedTable.auto_increment == null) mergedTable.auto_increment = dsProps.auto_increment
    if (dsProps.create_time && !mergedTable.create_time) mergedTable.create_time = dsProps.create_time
    if (dsProps.update_time && !mergedTable.update_time) mergedTable.update_time = dsProps.update_time
    if (dsProps.create_options && !mergedTable.create_options) mergedTable.create_options = dsProps.create_options
  }

  // PostgreSQL: 동일한 이유로 ``postgresql.*`` key 를 pgTableProps 입력 형식으로 머지.
  if (isPostgres && datasetProperties && datasetProperties.length > 0) {
    const dsProps: Record<string, string> = {}
    for (const p of datasetProperties) {
      const key = p.property_key.startsWith("postgresql.") ? p.property_key.slice(11) : p.property_key
      dsProps[key] = p.property_value
    }
    if (dsProps.estimated_rows && mergedTable.estimated_rows == null) mergedTable.estimated_rows = dsProps.estimated_rows
    if (dsProps.table_size && mergedTable.table_size == null) mergedTable.table_size = dsProps.table_size
    if (dsProps.total_size && mergedTable.total_size == null) mergedTable.total_size = dsProps.total_size
    if (dsProps.index_size && mergedTable.index_size == null) mergedTable.index_size = dsProps.index_size
    if (dsProps.tablespace && !mergedTable.tablespace) mergedTable.tablespace = dsProps.tablespace
  }

  const tableProps = isHiveImpala
    ? hiveImpalaTableProps(mergedTable)
    : isMySQL ? mysqlTableProps(mergedTable) : pgTableProps(mergedTable)

  const hasContent = isHiveImpala
    ? Object.keys(tblProperties).length > 0
    : (tableProps.length > 0 || Object.keys(columns).length > 0 || indexes.length > 0)

  if (!hasContent) {
    return null
  }

  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Settings2 className="h-4 w-4" />
          Datasource Specific
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Table-level properties — MySQL/PostgreSQL. Hive/Impala/Iceberg 와 동일하게
            AG Grid Key/Value 표로 통일해 긴 value 가 layout 을 깨지 않도록 한다. */}
        {!isHiveImpala && tableProps.length > 0 && (
          <TblPropertiesGrid
            tblProperties={Object.fromEntries(tableProps.map((p) => [p.label, p.value]))}
          />
        )}

        {/* COLUMNS (AG Grid, collapsed) — MySQL/PostgreSQL */}
        {!isHiveImpala && <ColumnsGrid datasourceType={datasourceType} columns={columns} />}

        {/* INDEXES (AG Grid, collapsed) — PostgreSQL only */}
        {!isMySQL && !isHiveImpala && <IndexesGrid indexes={indexes} />}

        {/* TABLE PROPERTIES (AG Grid) — Hive/Impala */}
        {isHiveImpala && <TblPropertiesGrid tblProperties={tblProperties} />}
      </CardContent>
    </Card>
  )
}
