"use client"

/**
 * NiFi 2 Flow Tab
 *
 * Generates a NiFi 2.x Flow Definition JSON based on the actual nifi_db_hdfs.json template.
 * Pipeline: GenerateFlowFile → UpdateAttribute → ExecuteSQLRecord
 *           → PartitionRecord → ConvertRecord → PutParquet (HDFS)
 *
 * The JSON output follows the exact structure that NiFi 2.x expects for
 * process group upload, including all controller services (DBCP, AvroReader,
 * AvroWriter, ParquetReader, ParquetWriter).
 */

import { useCallback, useMemo, useState } from "react"
import {
  ReactFlow, Background, Controls,
  type Node, type Edge, Position, Handle, type NodeProps, ReactFlowProvider,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@workspace/ui/components/card"
import { Button } from "@workspace/ui/components/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { toast } from "sonner"
import type { DatasetDetail } from "@/features/datasets/data/schema"
import { CodeViewer } from "@/components/code-viewer"

// ---------------------------------------------------------------------------
// Processor node for React Flow visualization
// ---------------------------------------------------------------------------

function ProcessorNode({ data }: NodeProps) {
  const d = data as { label: string; color: string; properties: Record<string, string> }
  return (
    <div className="border-2 border-gray-300 rounded-lg bg-white shadow-md min-w-[220px]">
      <Handle type="target" position={Position.Left} className="!bg-gray-400 !w-2.5 !h-2.5" />
      <div className="px-3 py-2 bg-gray-100 rounded-t-md border-b flex items-center gap-2">
        <div className={`w-2.5 h-2.5 rounded-full ${d.color}`} />
        <span className="text-xs font-bold text-gray-700">{d.label}</span>
      </div>
      <div className="px-3 py-2 space-y-0.5">
        {Object.entries(d.properties).map(([k, v]) => (
          <div key={k} className="flex gap-1 text-[10px]">
            <span className="text-gray-400 shrink-0">{k}:</span>
            <span className="text-gray-600 font-medium truncate max-w-[180px]" title={v}>{v}</span>
          </div>
        ))}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-gray-400 !w-2.5 !h-2.5" />
    </div>
  )
}

const nodeTypes = { processor: ProcessorNode }

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getDatasetParts(dataset: DatasetDetail) {
  const parts = dataset.name.split(".")
  const dbName = parts.length > 1 ? parts[0]! : "<database>"
  const tblName = parts.length > 1 ? parts[1]! : parts[0]!
  return { dbName, tblName, datasourceType: dataset.datasource.type, datasourceId: dataset.datasource.datasource_id }
}

function getJdbcConfig(datasourceType: string, dbName: string) {
  switch (datasourceType) {
    case "mysql": return { url: `jdbc:mysql://<HOST>:3306/${dbName}`, driver: "com.mysql.cj.jdbc.Driver", driverJar: "/usr/share/java/mysql-connector-java.jar" }
    case "postgresql": case "greenplum": return { url: `jdbc:postgresql://<HOST>:5432/${dbName}`, driver: "org.postgresql.Driver", driverJar: "/usr/share/java/postgresql.jar" }
    case "starrocks": return { url: `jdbc:mysql://<HOST>:9030/${dbName}`, driver: "com.mysql.cj.jdbc.Driver", driverJar: "/usr/share/java/mysql-connector-java.jar" }
    case "oracle": return { url: `jdbc:oracle:thin:@<HOST>:1521:${dbName}`, driver: "oracle.jdbc.OracleDriver", driverJar: "/usr/share/java/ojdbc8.jar" }
    case "trino": return { url: `jdbc:trino://<HOST>:8080/${dbName}`, driver: "io.trino.jdbc.TrinoDriver", driverJar: "/usr/share/java/trino-jdbc.jar" }
    default: return { url: `jdbc:${datasourceType}://<HOST>/<PORT>/${dbName}`, driver: "<DRIVER_CLASS>", driverJar: "/usr/share/java/" }
  }
}

function uid(seed: string): string {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = ((h << 5) - h + seed.charCodeAt(i)) | 0
  const hex = Math.abs(h).toString(16).padStart(12, "0")
  return `${hex.slice(0, 8)}-${hex.slice(0, 4)}-3${hex.slice(1, 4)}-a${hex.slice(2, 5)}-${hex.padEnd(12, "0").slice(0, 12)}`
}

// ---------------------------------------------------------------------------
// React Flow visualization (matches the actual 6-processor pipeline)
// ---------------------------------------------------------------------------

function buildVisualization(dataset: DatasetDetail): { nodes: Node[]; edges: Edge[] } {
  const { dbName, tblName, datasourceId } = getDatasetParts(dataset)
  const columns = dataset.schema_fields.map(f => f.field_path).join(", ")
  const colTrunc = columns.length > 40 ? columns.slice(0, 37) + "..." : columns
  const xGap = 320

  const nodes: Node[] = [
    { id: "1", type: "processor", position: { x: 0, y: 100 },
      data: { label: "GenerateFlowFile", color: "bg-green-500", properties: { "Batch Size": "1" } } },
    { id: "2", type: "processor", position: { x: xGap, y: 100 },
      data: { label: "UpdateAttribute", color: "bg-yellow-500", properties: {
        "query": `SELECT ${colTrunc} FROM ${dbName}.${tblName}`,
        "table": tblName, "db": dbName, "datasourceId": datasourceId,
      } } },
    { id: "3", type: "processor", position: { x: xGap * 2, y: 100 },
      data: { label: "ExecuteSQLRecord", color: "bg-orange-400", properties: { "SQL": "${query}", "Pool": `${datasourceId}-pool` } } },
    { id: "4", type: "processor", position: { x: 0, y: 300 },
      data: { label: "PartitionRecord", color: "bg-orange-400", properties: { "part_date": "${partition}" } } },
    { id: "5", type: "processor", position: { x: xGap, y: 300 },
      data: { label: "ConvertRecord", color: "bg-orange-400", properties: { "Avro → Parquet": "" } } },
    { id: "6", type: "processor", position: { x: xGap * 2, y: 300 },
      data: { label: "PutParquet", color: "bg-blue-500", properties: {
        "Directory": `hdfs://nameservice/data/catalog/${datasourceId}/${dbName}/${tblName}/\${part_date}`,
      } } },
  ]

  const edgeStyle = { stroke: "#94a3b8" }
  const edges: Edge[] = [
    { id: "e1", source: "1", target: "2", animated: true, style: edgeStyle },
    { id: "e2", source: "2", target: "3", animated: true, style: edgeStyle },
    { id: "e3", source: "3", target: "4", animated: true, style: edgeStyle },
    { id: "e4", source: "4", target: "5", animated: true, style: edgeStyle },
    { id: "e5", source: "5", target: "6", animated: true, style: edgeStyle },
  ]

  return { nodes, edges }
}

// ---------------------------------------------------------------------------
// NiFi 2 Flow JSON generator (based on nifi_db_hdfs.json template)
// ---------------------------------------------------------------------------

function generateNiFiFlowJson(dataset: DatasetDetail): string {
  const { dbName, tblName, datasourceType, datasourceId } = getDatasetParts(dataset)
  const jdbc = getJdbcConfig(datasourceType, dbName)
  const columns = dataset.schema_fields.map(f => f.field_path).join(", ")
  const groupId = uid(`${datasourceId}-${dbName}-${tblName}-group`)

  // Controller Service IDs
  const dbcpId = uid(`${datasourceId}-dbcp`)
  const csvWriterId = uid(`${datasourceId}-csvwriter`)
  const avroWriterPartId = uid(`${datasourceId}-avrowriter-part`)
  const avroWriterSchemaId = uid(`${datasourceId}-avrowriter-schema`)
  const avroReaderId = uid(`${datasourceId}-avroreader`)
  const avroReader2Id = uid(`${datasourceId}-avroreader2`)
  const parquetReaderId = uid(`${datasourceId}-parquetreader`)
  const parquetWriterId = uid(`${datasourceId}-parquetwriter`)

  // Processor IDs
  const genFlowFileId = uid(`${datasourceId}-${tblName}-genflowfile`)
  const updateAttrId = uid(`${datasourceId}-${tblName}-updateattr`)
  const execSqlId = uid(`${datasourceId}-${tblName}-execsql`)
  const partitionId = uid(`${datasourceId}-${tblName}-partition`)
  const convertId = uid(`${datasourceId}-${tblName}-convert`)
  const putParquetId = uid(`${datasourceId}-${tblName}-putparquet`)

  const flow = {
    flowContents: {
      identifier: groupId,
      name: `${datasourceId}-${dbName}-${tblName}-ingestion`,
      comments: `Auto-generated by Argus Catalog for ${dataset.name}`,
      position: { x: 0, y: 0 },
      processGroups: [],
      remoteProcessGroups: [],
      processors: [
        {
          identifier: genFlowFileId,
          name: "GenerateFlowFile",
          type: "org.apache.nifi.processors.standard.GenerateFlowFile",
          bundle: { group: "org.apache.nifi", artifact: "nifi-standard-nar", version: "2.0.0" },
          properties: { "File Size": "0B", "Batch Size": "1", "Unique FlowFiles": "false", "Data Format": "Text" },
          propertyDescriptors: {},
          schedulingPeriod: "1 min", schedulingStrategy: "TIMER_DRIVEN", executionNode: "ALL",
          penaltyDuration: "30 sec", yieldDuration: "1 sec", bulletinLevel: "WARN",
          runDurationMillis: 0, concurrentlySchedulableTaskCount: 1,
          autoTerminatedRelationships: [], scheduledState: "ENABLED", retryCount: 10, retriedRelationships: [],
          position: { x: -712, y: -568 },
        },
        {
          identifier: updateAttrId,
          name: "UpdateAttribute",
          type: "org.apache.nifi.processors.attributes.UpdateAttribute",
          bundle: { group: "org.apache.nifi", artifact: "nifi-update-attribute-nar", version: "2.0.0" },
          properties: {
            "Store State": "Do not store state",
            "Cache Value Lookup Cache Size": "100",
            query: `SELECT ${columns} FROM ${dbName}.${tblName}`,
            tableName: tblName,
            dbName: dbName,
            datasource_id: datasourceId,
            tablePartition: "dt",
            avroSchema: "${avro.schema}",
            sqlQuery: `SELECT ${columns} FROM ${dbName}.${tblName}`,
          },
          propertyDescriptors: {},
          schedulingPeriod: "0 sec", schedulingStrategy: "TIMER_DRIVEN", executionNode: "ALL",
          penaltyDuration: "30 sec", yieldDuration: "1 sec", bulletinLevel: "WARN",
          runDurationMillis: 0, concurrentlySchedulableTaskCount: 1,
          autoTerminatedRelationships: ["failure"], scheduledState: "ENABLED", retryCount: 10, retriedRelationships: [],
          position: { x: -712, y: -320 },
        },
        {
          identifier: execSqlId,
          name: "ExecuteSQLRecord",
          type: "org.apache.nifi.processors.standard.ExecuteSQLRecord",
          bundle: { group: "org.apache.nifi", artifact: "nifi-standard-nar", version: "2.0.0" },
          properties: {
            "Database Connection Pooling Service": dbcpId,
            "SQL Query": "${query}",
            "Record Writer": csvWriterId,
            "Max Wait Time": "0 seconds",
            "Normalize Table/Column Names": "false",
            "Use Avro Logical Types": "false",
            "Default Decimal Precision": "10",
            "Default Decimal Scale": "0",
            "Max Rows Per FlowFile": "0",
            "Output Batch Size": "0",
            "Fetch Size": "0",
            "Set Auto Commit": "true",
          },
          propertyDescriptors: {},
          schedulingPeriod: "0 sec", schedulingStrategy: "TIMER_DRIVEN", executionNode: "ALL",
          penaltyDuration: "30 sec", yieldDuration: "1 sec", bulletinLevel: "WARN",
          runDurationMillis: 0, concurrentlySchedulableTaskCount: 1,
          autoTerminatedRelationships: ["failure"], scheduledState: "ENABLED", retryCount: 10, retriedRelationships: [],
          position: { x: -584, y: -72 },
        },
        {
          identifier: partitionId,
          name: "PartitionRecord",
          type: "org.apache.nifi.processors.standard.PartitionRecord",
          bundle: { group: "org.apache.nifi", artifact: "nifi-standard-nar", version: "2.0.0" },
          properties: {
            "Record Reader": avroReader2Id,
            "Record Writer": avroWriterPartId,
            part_date: "${partition}",
          },
          propertyDescriptors: {},
          schedulingPeriod: "0 sec", schedulingStrategy: "TIMER_DRIVEN", executionNode: "ALL",
          penaltyDuration: "30 sec", yieldDuration: "1 sec", bulletinLevel: "WARN",
          runDurationMillis: 0, concurrentlySchedulableTaskCount: 1,
          autoTerminatedRelationships: ["failure", "original"], scheduledState: "ENABLED", retryCount: 10, retriedRelationships: [],
          position: { x: -896, y: 128 },
        },
        {
          identifier: convertId,
          name: "ConvertRecord",
          type: "org.apache.nifi.processors.standard.ConvertRecord",
          bundle: { group: "org.apache.nifi", artifact: "nifi-standard-nar", version: "2.0.0" },
          properties: {
            "Record Reader": avroReaderId,
            "Record Writer": parquetWriterId,
            "Include Zero Record FlowFiles": "true",
          },
          propertyDescriptors: {},
          schedulingPeriod: "0 sec", schedulingStrategy: "TIMER_DRIVEN", executionNode: "ALL",
          penaltyDuration: "30 sec", yieldDuration: "1 sec", bulletinLevel: "WARN",
          runDurationMillis: 0, concurrentlySchedulableTaskCount: 1,
          autoTerminatedRelationships: ["failure"], scheduledState: "ENABLED", retryCount: 10, retriedRelationships: [],
          position: { x: -800, y: 376 },
        },
        {
          identifier: putParquetId,
          name: "PutParquet",
          type: "org.apache.nifi.parquet.PutParquet",
          bundle: { group: "org.apache.nifi", artifact: "nifi-parquet-nar", version: "2.0.0" },
          properties: {
            "Hadoop Configuration Resources": "/etc/hadoop/core-site.xml,/etc/hadoop/hdfs-site.xml",
            "Record Reader": parquetReaderId,
            Directory: `hdfs://nameservice/data/catalog/${datasourceId}/${dbName}/${tblName}/\${part_date}`,
            "Compression Type": "SNAPPY",
            "Overwrite Files": "false",
            "Remove CRC Files": "false",
          },
          propertyDescriptors: {},
          schedulingPeriod: "0 sec", schedulingStrategy: "TIMER_DRIVEN", executionNode: "ALL",
          penaltyDuration: "30 sec", yieldDuration: "1 sec", bulletinLevel: "WARN",
          runDurationMillis: 0, concurrentlySchedulableTaskCount: 1,
          autoTerminatedRelationships: ["failure", "retry", "success"], scheduledState: "ENABLED", retryCount: 10, retriedRelationships: [],
          position: { x: -304, y: 168 },
        },
      ],
      inputPorts: [], outputPorts: [], labels: [], funnels: [],
      connections: [
        { identifier: uid("c1"), name: "", source: { id: genFlowFileId, type: "PROCESSOR", groupId }, destination: { id: updateAttrId, type: "PROCESSOR", groupId }, selectedRelationships: ["success"], backPressureObjectThreshold: 10000, backPressureDataSizeThreshold: "1 GB", flowFileExpiration: "0 sec", loadBalanceStrategy: "DO_NOT_LOAD_BALANCE", loadBalanceCompression: "DO_NOT_COMPRESS", zIndex: 0 },
        { identifier: uid("c2"), name: "", source: { id: updateAttrId, type: "PROCESSOR", groupId }, destination: { id: execSqlId, type: "PROCESSOR", groupId }, selectedRelationships: ["success"], backPressureObjectThreshold: 10000, backPressureDataSizeThreshold: "1 GB", flowFileExpiration: "0 sec", loadBalanceStrategy: "DO_NOT_LOAD_BALANCE", loadBalanceCompression: "DO_NOT_COMPRESS", zIndex: 0 },
        { identifier: uid("c3"), name: "", source: { id: execSqlId, type: "PROCESSOR", groupId }, destination: { id: partitionId, type: "PROCESSOR", groupId }, selectedRelationships: ["success"], backPressureObjectThreshold: 10000, backPressureDataSizeThreshold: "1 GB", flowFileExpiration: "0 sec", loadBalanceStrategy: "DO_NOT_LOAD_BALANCE", loadBalanceCompression: "DO_NOT_COMPRESS", zIndex: 0 },
        { identifier: uid("c4"), name: "", source: { id: partitionId, type: "PROCESSOR", groupId }, destination: { id: convertId, type: "PROCESSOR", groupId }, selectedRelationships: ["success"], backPressureObjectThreshold: 10000, backPressureDataSizeThreshold: "1 GB", flowFileExpiration: "0 sec", loadBalanceStrategy: "DO_NOT_LOAD_BALANCE", loadBalanceCompression: "DO_NOT_COMPRESS", zIndex: 0 },
        { identifier: uid("c5"), name: "", source: { id: convertId, type: "PROCESSOR", groupId }, destination: { id: putParquetId, type: "PROCESSOR", groupId }, selectedRelationships: ["success"], backPressureObjectThreshold: 10000, backPressureDataSizeThreshold: "1 GB", flowFileExpiration: "0 sec", loadBalanceStrategy: "DO_NOT_LOAD_BALANCE", loadBalanceCompression: "DO_NOT_COMPRESS", zIndex: 0 },
      ],
      controllerServices: [
        {
          identifier: dbcpId, name: `${datasourceId}-pool`,
          type: "org.apache.nifi.dbcp.DBCPConnectionPool",
          bundle: { group: "org.apache.nifi", artifact: "nifi-dbcp-service-nar", version: "2.0.0" },
          properties: {
            "Database Connection URL": jdbc.url,
            "Database Driver Class Name": jdbc.driver,
            "Database Driver Locations": jdbc.driverJar,
            "Database User": "<USERNAME>",
            Password: "<PASSWORD>",
            "Max Wait Time": "500 millis",
            "Max Total Connections": "8",
            "Maximum Idle Connections": "8",
            "Minimum Idle Connections": "0",
            "Password Source": "PASSWORD",
          },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
        {
          identifier: csvWriterId, name: "CSVRecordSetWriter",
          type: "org.apache.nifi.csv.CSVRecordSetWriter",
          bundle: { group: "org.apache.nifi", artifact: "nifi-record-serialization-services-nar", version: "2.0.0" },
          properties: { "Schema Write Strategy": "no-schema", "Schema Access Strategy": "inherit-record-schema", "CSV Format": "custom", "Value Separator": ",", "Include Header Line": "true", "Quote Character": "\"" },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
        {
          identifier: avroWriterPartId, name: "AvroRecordSetWriter (partition)",
          type: "org.apache.nifi.avro.AvroRecordSetWriter",
          bundle: { group: "org.apache.nifi", artifact: "nifi-record-serialization-services-nar", version: "2.0.0" },
          properties: { "Schema Write Strategy": "avro-embedded", "Schema Access Strategy": "inherit-record-schema", "Compression Format": "NONE" },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
        {
          identifier: avroWriterSchemaId, name: "AvroRecordSetWriter (schema)",
          type: "org.apache.nifi.avro.AvroRecordSetWriter",
          bundle: { group: "org.apache.nifi", artifact: "nifi-record-serialization-services-nar", version: "2.0.0" },
          properties: { "Schema Write Strategy": "full-schema-attribute", "Schema Access Strategy": "schema-text-property", "Schema Text": "${avroSchema}", "Compression Format": "NONE" },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
        {
          identifier: avroReaderId, name: "AvroReader (embedded)",
          type: "org.apache.nifi.avro.AvroReader",
          bundle: { group: "org.apache.nifi", artifact: "nifi-record-serialization-services-nar", version: "2.0.0" },
          properties: { "Schema Access Strategy": "embedded-avro-schema", "Cache Size": "1000", "Fast Reader Enabled": "true" },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
        {
          identifier: avroReader2Id, name: "AvroReader (embedded) 2",
          type: "org.apache.nifi.avro.AvroReader",
          bundle: { group: "org.apache.nifi", artifact: "nifi-record-serialization-services-nar", version: "2.0.0" },
          properties: { "Schema Access Strategy": "embedded-avro-schema", "Cache Size": "1000", "Fast Reader Enabled": "true" },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
        {
          identifier: parquetReaderId, name: "ParquetReader",
          type: "org.apache.nifi.parquet.ParquetReader",
          bundle: { group: "org.apache.nifi", artifact: "nifi-parquet-nar", version: "2.0.0" },
          properties: { "Avro Read Compatibility": "true", "Avro Add List Element Records": "true" },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
        {
          identifier: parquetWriterId, name: "ParquetRecordSetWriter",
          type: "org.apache.nifi.parquet.ParquetRecordSetWriter",
          bundle: { group: "org.apache.nifi", artifact: "nifi-parquet-nar", version: "2.0.0" },
          properties: { "Schema Write Strategy": "no-schema", "Schema Access Strategy": "inherit-record-schema", "Compression Type": "SNAPPY" },
          propertyDescriptors: {}, scheduledState: "DISABLED",
        },
      ],
      defaultFlowFileExpiration: "0 sec",
      defaultBackPressureObjectThreshold: 10000,
      defaultBackPressureDataSizeThreshold: "1 GB",
      scheduledState: "ENABLED",
      executionEngine: "STANDARD",
      maxConcurrentTasks: 1,
      statelessFlowTimeout: "1 min",
      componentType: "PROCESS_GROUP",
    },
  }

  return JSON.stringify(flow, null, 2)
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

type NiFiFlowTabProps = { dataset: DatasetDetail }

function NiFiFlowInner({ dataset }: NiFiFlowTabProps) {
  const { nodes, edges } = useMemo(() => buildVisualization(dataset), [dataset])
  const flowJson = useMemo(() => generateNiFiFlowJson(dataset), [dataset])
  const [showJson, setShowJson] = useState(false)
  const jsonLineCount = useMemo(() => flowJson.split("\n").length, [flowJson])

  const handleCopyJson = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(flowJson)
      toast.success("NiFi flow JSON 을 클립보드에 복사했습니다.")
    } catch {
      toast.error("복사 실패. Clipboard API 는 HTTPS 가 필요합니다.")
    }
  }, [flowJson])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div>
          <CardTitle className="text-base">NiFi 2 Flow</CardTitle>
          <CardDescription className="text-xs mt-1">
            GenerateFlowFile → UpdateAttribute → ExecuteSQLRecord → PartitionRecord → ConvertRecord → PutParquet (HDFS)
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setShowJson(true)}>Show Flow JSON</Button>
          <Button size="sm" variant="outline" onClick={handleCopyJson}>Copy Flow JSON</Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="border-t" style={{ height: 450 }}>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes}
            fitView fitViewOptions={{ padding: 0.3 }} proOptions={{ hideAttribution: true }}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} panOnDrag zoomOnScroll>
            <Background gap={16} size={1} color="#e5e7eb" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </CardContent>

      <Dialog open={showJson} onOpenChange={setShowJson}>
        <DialogContent className="max-w-4xl max-h-[85vh] p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b">
            <DialogTitle>NiFi 2 Flow JSON</DialogTitle>
          </DialogHeader>
          <CodeViewer
            code={flowJson}
            language="json"
            height={Math.min(jsonLineCount * 20 + 20, 600)}
            copyLabel="NiFi Flow JSON"
            className="rounded-none"
          />
        </DialogContent>
      </Dialog>
    </Card>
  )
}

export function NiFiFlowTab({ dataset }: NiFiFlowTabProps) {
  return <ReactFlowProvider><NiFiFlowInner dataset={dataset} /></ReactFlowProvider>
}
