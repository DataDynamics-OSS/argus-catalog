"use client"

import { useModels } from "./models-provider"
import { ModelsTable } from "./models-table"

export function ModelsTableWrapper() {
  const { models, isLoading } = useModels()
  return <ModelsTable data={models} isLoading={isLoading} />
}
