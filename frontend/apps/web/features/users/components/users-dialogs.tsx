/**
 * Users Dialogs Orchestrator component.
 *
 * Centrally manages all dialog instances for the user management feature.
 * Reads the current dialog state (`open`) and the associated data (`currentRow`,
 * `selectedUsers`) from the UsersProvider context, then conditionally renders
 * the appropriate dialog component:
 *
 * - **"add"**: UsersActionDialog in add mode (no currentRow).
 * - **"edit"**: UsersActionDialog in edit mode (with currentRow). Only rendered
 *   when a currentRow is set. Uses a dynamic key to force re-mount when
 *   switching between different users.
 * - **"delete"**: UsersDeleteDialog. Only rendered when a currentRow is set.
 *   Requires username confirmation before deletion.
 * - **"activate"**: UsersStatusDialog for bulk activation of selected users.
 * - **"deactivate"**: UsersStatusDialog for bulk deactivation of selected users.
 *
 * When the edit or delete dialog closes, a 500ms timeout clears `currentRow`
 * to allow the closing animation to complete before resetting the data.
 */

"use client"

import { UsersActionDialog } from "./users-action-dialog"
import { UsersChangePasswordDialog } from "./users-change-password-dialog"
import { UsersDeleteDialog } from "./users-delete-dialog"
import { UsersStatusDialog } from "./users-status-dialog"
import { useUsers } from "./users-provider"

export function UsersDialogs() {
  const { open, setOpen, currentRow, setCurrentRow, selectedUsers } = useUsers()
  return (
    <>
      {/* Add new user dialog (always available, no currentRow needed) */}
      <UsersActionDialog
        key="user-add"
        open={open === "add"}
        onOpenChange={() => setOpen("add")}
      />

      {/* Bulk activate dialog — operates on selectedUsers from table checkboxes */}
      <UsersStatusDialog
        key="user-activate"
        open={open === "activate"}
        onOpenChange={() => setOpen("activate")}
        selectedUsers={selectedUsers}
        type="activate"
      />

      {/* Bulk deactivate dialog — operates on selectedUsers from table checkboxes */}
      <UsersStatusDialog
        key="user-deactivate"
        open={open === "deactivate"}
        onOpenChange={() => setOpen("deactivate")}
        selectedUsers={selectedUsers}
        type="deactivate"
      />

      {/* Edit and Delete dialogs — only rendered when a specific row is selected */}
      {currentRow && (
        <>
          <UsersActionDialog
            key={`user-edit-${currentRow.id}`}
            open={open === "edit"}
            onOpenChange={() => {
              setOpen("edit")
              // Delay clearing currentRow to allow the dialog close animation to finish
              setTimeout(() => {
                setCurrentRow(null)
              }, 500)
            }}
            currentRow={currentRow}
          />

          <UsersDeleteDialog
            key={`user-delete-${currentRow.id}`}
            open={open === "delete"}
            onOpenChange={() => {
              setOpen("delete")
              // Delay clearing currentRow to allow the dialog close animation to finish
              setTimeout(() => {
                setCurrentRow(null)
              }, 500)
            }}
            currentRow={currentRow}
          />

          {/* Change password dialog — admin resets the selected user's password */}
          <UsersChangePasswordDialog
            key={`user-password-${currentRow.id}`}
            open={open === "changePassword"}
            onOpenChange={() => {
              setOpen("changePassword")
              // Delay clearing currentRow to allow the dialog close animation to finish
              setTimeout(() => {
                setCurrentRow(null)
              }, 500)
            }}
            currentRow={currentRow}
          />
        </>
      )}
    </>
  )
}
