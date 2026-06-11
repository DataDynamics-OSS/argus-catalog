/**
 * Users Context Provider.
 *
 * Central state management for the User Management feature. This provider wraps
 * the users page and exposes all shared state through React Context, including:
 *
 * - **User data**: The current page of users fetched from the backend.
 * - **Pagination**: Current page number, page size, and total count.
 * - **Dialog control**: Which dialog is currently open (add/edit/delete/activate/deactivate)
 *   and which user row triggered it.
 * - **Selection**: The list of users selected via table checkboxes (for bulk operations).
 * - **Search/Filter**: Functions to trigger server-side search with applied filters.
 *
 * The provider uses a ref (`appliedFiltersRef`) to persist filter state across
 * pagination changes without triggering unnecessary re-renders. Filters are only
 * applied to the backend query when the user explicitly clicks the Search button.
 */

"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"

import useDialogState from "@/hooks/use-dialog-state"
import { fetchUsers, type PaginatedUsers } from "../api"
import { type User } from "../data/schema"

/**
 * Discriminated union of all dialog types that can be opened in the users feature.
 * - "add":            Open the user creation form.
 * - "edit":           Open the user edit form for a specific row.
 * - "delete":         Open the delete confirmation dialog for a specific row.
 * - "changePassword": Open the admin password-reset dialog for a specific row.
 * - "activate":       Open the bulk activate confirmation dialog.
 * - "deactivate":     Open the bulk deactivate confirmation dialog.
 */
type UsersDialogType = "add" | "edit" | "delete" | "changePassword" | "activate" | "deactivate"

/** Parameters sent to the backend when performing a filtered search. */
type SearchParams = {
  /** Single status filter value ("active" or "inactive"), or null for no filter. */
  status: string | null
  /** Role name filter (e.g. "Admin", "User"), or empty string for no filter. */
  role: string
  /** Free-text search string applied to username, name, email, and phone. */
  search: string
  /** Organization filter (exact), or empty string for no filter. */
  organization: string
}

/** Shape of the context value exposed to all consumers via `useUsers()`. */
type UsersContextType = {
  /** Currently open dialog type, or null if no dialog is open. */
  open: UsersDialogType | null
  /** Open or close a specific dialog type. */
  setOpen: (str: UsersDialogType | null) => void
  /** The user row that triggered a row-level action (edit/delete), or null. */
  currentRow: User | null
  /** Set the current row for row-level actions. */
  setCurrentRow: React.Dispatch<React.SetStateAction<User | null>>
  /** Users currently selected via table checkboxes (for bulk actions). */
  selectedUsers: User[]
  /** Update the selected users list. */
  setSelectedUsers: React.Dispatch<React.SetStateAction<User[]>>
  /** Array of User objects for the current page. */
  users: User[]
  /** Total number of users matching the current filters (for pagination). */
  total: number
  /** Current page number (1-based). */
  page: number
  /** Number of items displayed per page. */
  pageSize: number
  /** Whether a fetch request is currently in progress. */
  isLoading: boolean
  /** Navigate to a specific page number. Triggers a backend fetch. */
  setPage: (page: number) => void
  /** Change the page size. Resets to page 1 and triggers a backend fetch. */
  setPageSize: (size: number) => void
  /** Apply filters and trigger a server-side search. Resets to page 1. */
  searchUsers: (params: SearchParams) => void
  /** Re-fetch the current page with the last applied filters. */
  refreshUsers: () => Promise<void>
}

const UsersContext = React.createContext<UsersContextType | null>(null)

/**
 * Provider component that manages all user-related state.
 *
 * Wraps the users page content and makes state available via `useUsers()`.
 * Handles data fetching, pagination, and dialog lifecycle.
 */
export function UsersProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useDialogState<UsersDialogType>(null)
  const [currentRow, setCurrentRow] = useState<User | null>(null)
  const [selectedUsers, setSelectedUsers] = useState<User[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [isLoading, setIsLoading] = useState(true)

  /**
   * Ref to persist the last applied filter values.
   *
   * Using a ref instead of state avoids re-renders when filters change,
   * since filters should only take effect when the Search button is clicked.
   * Pagination handlers read from this ref to maintain filters across pages.
   */
  const appliedFiltersRef = useRef<SearchParams>({ status: null, role: "", search: "", organization: "" })

  /**
   * Core data fetching function.
   *
   * Calls the `fetchUsers` API with the given pagination and filter parameters,
   * then updates the local state with the response data. Wrapped in useCallback
   * to provide a stable reference for dependent hooks.
   */
  const loadUsers = useCallback(async (params: {
    page: number
    pageSize: number
    status: string | null
    role: string
    search: string
    organization: string
  }) => {
    try {
      setIsLoading(true)
      const data: PaginatedUsers = await fetchUsers({
        page: params.page,
        pageSize: params.pageSize,
        status: params.status || undefined,
        role: params.role || undefined,
        search: params.search || undefined,
        organization: params.organization || undefined,
      })
      setUsers(data.items)
      setTotal(data.total)
    } catch (err) {
      console.error("Failed to fetch users:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Fetch the first page of users on initial mount.
  useEffect(() => {
    loadUsers({ page: 1, pageSize, status: null, role: "", search: "", organization: "" })
  }, [loadUsers, pageSize])

  /**
   * Handle page navigation.
   * Preserves the currently applied filters while changing only the page number.
   */
  const handleSetPage = useCallback((newPage: number) => {
    setPage(newPage)
    const f = appliedFiltersRef.current
    loadUsers({ page: newPage, pageSize, status: f.status, role: f.role, search: f.search, organization: f.organization })
  }, [loadUsers, pageSize])

  /**
   * Handle page size change.
   * Resets to page 1 and preserves the currently applied filters.
   */
  const handleSetPageSize = useCallback((size: number) => {
    setPageSize(size)
    setPage(1)
    const f = appliedFiltersRef.current
    loadUsers({ page: 1, pageSize: size, status: f.status, role: f.role, search: f.search, organization: f.organization })
  }, [loadUsers])

  /**
   * Execute a search with the given filter parameters.
   *
   * Called when the user clicks the Search button in the toolbar.
   * Stores the filters in the ref for use by pagination handlers,
   * resets to page 1, and triggers a fresh fetch.
   */
  const searchUsers = useCallback((params: SearchParams) => {
    appliedFiltersRef.current = params
    setPage(1)
    loadUsers({ page: 1, pageSize, status: params.status, role: params.role, search: params.search, organization: params.organization })
  }, [loadUsers, pageSize])

  /**
   * Refresh the current view without changing page or filters.
   *
   * Typically called after a mutation (create, delete, activate, deactivate)
   * to reflect the latest data from the backend.
   */
  const refreshUsers = useCallback(async () => {
    const f = appliedFiltersRef.current
    await loadUsers({ page, pageSize, status: f.status, role: f.role, search: f.search, organization: f.organization })
  }, [loadUsers, page, pageSize])

  return (
    <UsersContext value={{
      open, setOpen,
      currentRow, setCurrentRow,
      selectedUsers, setSelectedUsers,
      users, total, page, pageSize, isLoading,
      setPage: handleSetPage, setPageSize: handleSetPageSize,
      searchUsers,
      refreshUsers,
    }}>
      {children}
    </UsersContext>
  )
}

/**
 * Custom hook to access the Users context.
 *
 * Must be called within a `<UsersProvider>` component tree.
 * Throws an error if used outside the provider to catch misuse early.
 *
 * @returns The full UsersContextType with all state and actions.
 */
export const useUsers = () => {
  const usersContext = React.useContext(UsersContext)

  if (!usersContext) {
    throw new Error("useUsers has to be used within <UsersProvider>")
  }

  return usersContext
}

/**
 * Variant of `useUsers` that returns `null` instead of throwing when called
 * outside a `<UsersProvider>`. For components that are used both inside and
 * outside the provider tree.
 *
 * @returns The UsersContextType, or `null` when no provider is present.
 */
export const useUsersOptional = () => {
  return React.useContext(UsersContext)
}
