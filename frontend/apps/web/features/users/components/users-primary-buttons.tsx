/**
 * Users Primary Action Buttons component.
 *
 * Renders a single "Action" dropdown button that contains:
 * - **Activate**: Opens the bulk activate confirmation dialog for selected users.
 * - **Deactivate**: Opens the bulk deactivate confirmation dialog for selected users.
 * - **Add**: Opens the user creation form dialog.
 *
 * Dialog state is managed through the UsersProvider context.
 */

"use client"

import { ChevronDown } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@workspace/ui/components/dropdown-menu"
import { useUsers } from "./users-provider"

export function UsersPrimaryButtons() {
  const { setOpen } = useUsers()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" className="h-8 bg-blue-600 text-white hover:bg-blue-700">
          작업
          <ChevronDown className="ml-1 size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => setOpen("activate")}>
          활성화
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setOpen("deactivate")}>
          비활성화
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => setOpen("add")}>
          추가
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
