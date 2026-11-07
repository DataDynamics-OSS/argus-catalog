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
import { notifyError } from "@/lib/notify"
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

/** 서버 정렬 상태 — 표 열 id 와 방향. null 이면 서버 기본(생성일 최신순). */
export type UsersSort = { id: string; desc: boolean } | null

/** 표 열 id → 서버 sort 파라미터. 여기에 없는 열은 정렬할 수 없다. */
export const USERS_SORT_FIELDS: Record<string, string> = {
  username: "username",
  fullName: "name",
  email: "email",
  organization: "organization",
  department: "department",
  status: "status",
  role: "role",
  createdAt: "created_at",
  lastLoginAt: "last_login_at",
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
  /** 현재 서버 정렬. */
  sort: UsersSort
  /** 정렬 변경 — 전체 결과 기준으로 서버에서 다시 정렬해 1페이지부터 보여 준다. */
  setSort: (sort: UsersSort) => void
}

const UsersContext = React.createContext<UsersContextType | null>(null)

const DEFAULT_PAGE_SIZE = 10

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
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [isLoading, setIsLoading] = useState(true)
  const [sort, setSortState] = useState<UsersSort>(null)
  // loadUsers 가 항상 최신 정렬을 쓰도록 ref 로도 들고 있는다 (페이지 이동·검색·새로 고침 공통).
  const sortRef = useRef<UsersSort>(null)

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
  // 마지막 요청 번호 — 요청이 겹치면(페이지 크기 변경·검색·페이지 이동 연속) 가장 마지막 요청의 응답만 반영.
  const requestSeqRef = useRef(0)

  const loadUsers = useCallback(async (params: {
    page: number
    pageSize: number
    status: string | null
    role: string
    search: string
    organization: string
  }) => {
    const seq = ++requestSeqRef.current
    try {
      setIsLoading(true)
      const data: PaginatedUsers = await fetchUsers({
        page: params.page,
        pageSize: params.pageSize,
        status: params.status || undefined,
        role: params.role || undefined,
        search: params.search || undefined,
        organization: params.organization || undefined,
        sort: sortRef.current ? USERS_SORT_FIELDS[sortRef.current.id] : undefined,
        order: sortRef.current?.desc ? "desc" : "asc",
      })
      if (seq !== requestSeqRef.current) return
      setUsers(data.items)
      setTotal(data.total)
    } catch (err) {
      if (seq !== requestSeqRef.current) return
      console.error("Failed to fetch users:", err)
      notifyError(err, "사용자 목록을 불러오지 못했습니다.")
    } finally {
      if (seq === requestSeqRef.current) setIsLoading(false)
    }
  }, [])

  // Fetch the first page of users on initial mount only. pageSize 를 의존성에 두면 페이지 크기 변경 시
  // 필터 없는 조회가 한 번 더 나가 handleSetPageSize 의 (필터 유지) 조회 결과를 덮어쓸 수 있다.
  useEffect(() => {
    loadUsers({ page: 1, pageSize: DEFAULT_PAGE_SIZE, status: null, role: "", search: "", organization: "" })
  }, [loadUsers])

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

  const handleSetSort = useCallback((next: UsersSort) => {
    sortRef.current = next && USERS_SORT_FIELDS[next.id] ? next : null
    setSortState(sortRef.current)
    setPage(1)
    const f = appliedFiltersRef.current
    loadUsers({ page: 1, pageSize, status: f.status, role: f.role, search: f.search, organization: f.organization })
  }, [loadUsers, pageSize])

  return (
    <UsersContext value={{
      open, setOpen,
      currentRow, setCurrentRow,
      selectedUsers, setSelectedUsers,
      users, total, page, pageSize, isLoading,
      setPage: handleSetPage, setPageSize: handleSetPageSize,
      searchUsers,
      refreshUsers,
      sort, setSort: handleSetSort,
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
