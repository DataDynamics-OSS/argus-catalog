import { describe, expect, it, vi } from "vitest"

vi.mock("@/features/change-mgmt/api", () => ({ listChangeRequests: vi.fn() }))

import type { CRStatus } from "@/features/change-mgmt/api"
import { summarizeChangeRequests } from "./dataset-delete-impact"

describe("summarizeChangeRequests", () => {
  it("counts all and in-flight change requests", () => {
    const statuses: CRStatus[] = [
      "DRAFT", "SUBMITTED", "APPROVING", "APPROVED", "SCHEDULED", "APPLIED", "CANCELLED",
    ]
    expect(summarizeChangeRequests(statuses.map((status) => ({ status })))).toEqual({ total: 7, inFlight: 4 })
  })

  it("returns zeros for no change requests", () => {
    expect(summarizeChangeRequests([])).toEqual({ total: 0, inFlight: 0 })
  })
})
