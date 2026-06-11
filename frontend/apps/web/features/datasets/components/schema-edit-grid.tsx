"use client"

/**
 * AG Grid based schema field editor.
 *
 * All edits are local (in-memory) until the parent calls save.
 * This prevents accidental DB updates — the user must explicitly
 * click "Update" to persist changes.
 */

import { useCallback, useContext, useEffect, useMemo, useRef, createContext } from "react"
import { AgGridReact } from "ag-grid-react"
import { AllCommunityModule, ModuleRegistry, type ColDef } from "ag-grid-community"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@workspace/ui/components/button"

ModuleRegistry.registerModules([AllCommunityModule])

// ---------------------------------------------------------------------------
// Types (shared with parent)
// ---------------------------------------------------------------------------

export type EditableField = {
  key: string
  field_path: string
  display_name: string
  field_type: string
  native_type: string
  description: string
  nullable: string
  is_primary_key: string
  is_unique: string
  is_indexed: string
  is_partition_key: string
  ordinal: number
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

// Context to pass delete handler to cell renderer
const DeleteContext = createContext<(key: string) => void>(() => {})

function DeleteCellRenderer(props: { value: string }) {
  const onDelete = useContext(DeleteContext)
  return (
    <button
      type="button"
      onClick={() => onDelete(props.value)}
      className="flex items-center justify-center w-full h-full text-muted-foreground hover:text-destructive transition-colors cursor-pointer"
    >
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  )
}

type SchemaEditGridProps = {
  fields: EditableField[]
  onChange: (fields: EditableField[]) => void
  dataTypeOptions?: string[]
}

export function SchemaEditGrid({ fields, onChange, dataTypeOptions = [] }: SchemaEditGridProps) {
  const gridRef = useRef<AgGridReact>(null)
  const fieldsRef = useRef(fields)
  // 렌더 중 ref 쓰기를 피하기 위해 커밋 후 동기화 — 이벤트 핸들러에서만 읽으므로 동작 동일.
  useEffect(() => {
    fieldsRef.current = fields
  }, [fields])

  const typeValues = useMemo(() =>
    dataTypeOptions.length > 0
      ? dataTypeOptions
      : ["STRING", "NUMBER", "BOOLEAN", "DATE", "BYTES", "MAP", "ARRAY", "ENUM"],
    [dataTypeOptions],
  )

  const handleDelete = useCallback((key: string) => {
    onChange(fieldsRef.current.filter(f => f.key !== key))
  }, [onChange])

  const columnDefs = useMemo<ColDef[]>(() => [
    {
      headerName: "#",
      valueGetter: (p) => (p.node?.rowIndex ?? 0) + 1,
      width: 50, maxWidth: 60,
      editable: false,
      sortable: false,
      cellStyle: { color: "#9ca3af", textAlign: "right" },
    },
    {
      headerName: "Field *",
      field: "field_path",
      minWidth: 140,
      editable: true,
      cellStyle: { fontWeight: 500 },
    },
    {
      headerName: "Logical Name",
      field: "display_name",
      minWidth: 140,
      editable: true,
    },
    {
      headerName: "Type *",
      field: "field_type",
      width: 120,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: typeValues },
    },
    {
      headerName: "Native Type",
      field: "native_type",
      width: 130,
      editable: true,
    },
    {
      headerName: "PK",
      field: "is_primary_key",
      width: 60,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["true", "false"] },
      cellRenderer: (p: { value: string }) => p.value === "true" ? "✓" : "",
      cellStyle: { textAlign: "center" },
    },
    {
      headerName: "Unique",
      field: "is_unique",
      width: 70,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["true", "false"] },
      cellRenderer: (p: { value: string }) => p.value === "true" ? "✓" : "",
      cellStyle: { textAlign: "center" },
    },
    {
      headerName: "Index",
      field: "is_indexed",
      width: 60,
      editable: false,
      cellRenderer: (p: { value: string }) => p.value === "true" ? "✓" : "",
      cellStyle: { textAlign: "center", color: "#9ca3af" },
    },
    {
      headerName: "Partition",
      field: "is_partition_key",
      width: 75,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["true", "false"] },
      cellRenderer: (p: { value: string }) => p.value === "true" ? "✓" : "",
      cellStyle: { textAlign: "center" },
    },
    {
      headerName: "Nullable",
      field: "nullable",
      width: 80,
      editable: true,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["true", "false"] },
      cellRenderer: (p: { value: string }) => p.value === "true" ? "Yes" : "No",
    },
    {
      headerName: "Description",
      field: "description",
      minWidth: 200,
      flex: 1,
      editable: true,
    },
    {
      headerName: "",
      field: "key",
      width: 50,
      maxWidth: 50,
      editable: false,
      sortable: false,
      cellRenderer: "deleteRenderer",
    },
  ] as ColDef[], [typeValues])

  const onCellValueChanged = useCallback(() => {
    const api = gridRef.current?.api
    if (!api) return
    const updated: EditableField[] = []
    api.forEachNode((node) => {
      if (node.data) {
        const row = { ...node.data }
        // Auto-compute: Index = PK || Unique
        row.is_indexed = (row.is_primary_key === "true" || row.is_unique === "true") ? "true" : "false"
        // PK implies Unique
        if (row.is_primary_key === "true") row.is_unique = "true"
        updated.push(row)
      }
    })
    onChange(updated)
    // Refresh Index column to reflect computed value
    setTimeout(() => api.refreshCells({ columns: ["is_indexed", "is_unique"] }), 0)
  }, [onChange])

  const addField = useCallback(() => {
    onChange([...fieldsRef.current, {
      key: `f-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      field_path: "",
      display_name: "",
      field_type: "STRING",
      native_type: "",
      description: "",
      nullable: "true",
      is_primary_key: "false",
      is_unique: "false",
      is_indexed: "false",
      is_partition_key: "false",
      ordinal: fieldsRef.current.length,
    }])
  }, [onChange])

  const components = useMemo(() => ({ deleteRenderer: DeleteCellRenderer }), [])

  return (
    <DeleteContext.Provider value={handleDelete}>
    <div className="space-y-2 p-4">
      <div
        className="border rounded ag-theme-alpine"
        style={{
          height: Math.min(fields.length * 34 + 44, 450),
          "--ag-font-family": "var(--font-d2coding), 'D2Coding', Consolas, monospace",
          "--ag-font-size": "13px",
        } as React.CSSProperties}
      >
        <AgGridReact
          ref={gridRef}
          columnDefs={columnDefs}
          rowData={fields}
          defaultColDef={{
            resizable: true,
            sortable: false,
            filter: false,
            minWidth: 60,
          }}
          headerHeight={32}
          rowHeight={30}
          singleClickEdit
          stopEditingWhenCellsLoseFocus
          onCellValueChanged={onCellValueChanged}
          animateRows={false}
          getRowId={(params) => params.data.key}
          components={components}
        />
      </div>
      <Button variant="outline" size="sm" onClick={addField}>
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add Field
      </Button>
    </div>
    </DeleteContext.Provider>
  )
}
