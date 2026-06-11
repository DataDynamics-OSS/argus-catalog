/**
 * Users Table Wrapper component.
 *
 * A thin wrapper that connects the UsersTable to the UsersProvider context.
 * It reads the `users` array and `isLoading` flag from the context and passes
 * them as props to the UsersTable component.
 *
 * This separation exists so that the UsersTable component can accept data as
 * props (making it easier to test in isolation), while the wrapper handles
 * the context integration.
 */

"use client"

import { UsersTable } from "./users-table"
import { useUsers } from "./users-provider"

export function UsersTableWrapper() {
  const { users, isLoading } = useUsers()

  return <UsersTable data={users} isLoading={isLoading} />
}
