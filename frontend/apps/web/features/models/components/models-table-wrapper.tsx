"use client"

import { useModels } from "./models-provider"
import { ModelsTable } from "./models-table"
import { ModelsDetail } from "./models-detail"

export function ModelsTableWrapper() {
  const { models, isLoading, selectedModelName, setSelectedModelName } = useModels()

  if (selectedModelName) {
    return (
      <ModelsDetail
        modelName={selectedModelName}
        onBack={() => setSelectedModelName(null)}
      />
    )
  }

  return <ModelsTable data={models} isLoading={isLoading} />
}
