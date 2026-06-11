/**
 * User Add/Edit Dialog component.
 *
 * A modal form dialog used for both creating new users and editing existing ones.
 * The dialog mode (add vs. edit) is determined by the presence of `currentRow`:
 *
 * - **Add mode** (currentRow is undefined): All fields are empty. Username and
 *   password are required. Uniqueness checks run on blur for username and email.
 * - **Edit mode** (currentRow is provided): Fields are pre-populated with the
 *   user's current data. Username is disabled (cannot be changed). Password
 *   fields are optional — leave blank to keep the existing password.
 *
 * Form validation uses Zod schema with multiple refinements:
 * 1. Password is required in add mode, optional in edit mode.
 * 2. Password must be at least 8 characters with at least one lowercase letter
 *    and one digit.
 * 3. Password and confirm password must match.
 *
 * On successful submission, the dialog calls the appropriate API function
 * (createUser or modifyUser) and refreshes the user list.
 */

"use client"

import { useCallback } from "react"
import { z } from "zod"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { toast } from "sonner"

import { Button } from "@workspace/ui/components/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@workspace/ui/components/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@workspace/ui/components/form"
import { Input } from "@workspace/ui/components/input"
import { PasswordInput } from "@/components/password-input"
import { SelectDropdown } from "@/components/select-dropdown"
import { changeMyPassword, changeUserRole, checkUserExists, createUser, modifyUser } from "../api"
import { roles } from "../data/data"
import { type User } from "../data/schema"
import { useUsersOptional } from "./users-provider"

/**
 * Zod validation schema for the user add/edit form.
 *
 * Uses an `isEdit` flag to conditionally relax password requirements in edit mode.
 * Multiple `.refine()` calls enforce password complexity rules:
 * - Required in add mode (non-empty after trim).
 * - Minimum 8 characters.
 * - At least one lowercase letter.
 * - At least one digit.
 * - Must match confirmPassword.
 *
 * In edit mode, if the password field is left blank, all password validations
 * are skipped (the existing password is preserved on the backend).
 */
const formSchema = z
  .object({
    firstName: z.string().min(1, "이름을 입력하세요."),
    lastName: z.string().min(1, "성을 입력하세요."),
    username: z.string().min(1, "사용자명을 입력하세요."),
    organization: z.string().optional().default(""),
    department: z.string().optional().default(""),
    phoneNumber: z.string().optional().default(""),
    email: z.string().min(1, "이메일을 입력하세요.").email("이메일 형식이 올바르지 않습니다."),
    password: z.string().transform((pwd) => pwd.trim()),
    role: z.string().min(1, "역할을 선택하세요."),
    confirmPassword: z.string().transform((pwd) => pwd.trim()),
    /** 계정 설정(본인 수정)에서 비밀번호 변경 시 필요한 현재 비밀번호. */
    currentPassword: z.string().transform((pwd) => pwd.trim()),
    /** Hidden flag indicating whether the form is in edit mode. */
    isEdit: z.boolean(),
    /** Hidden flag — 계정 설정(본인 수정) 모드 여부. */
    isSelf: z.boolean(),
  })
  .refine(
    (data) => {
      if (data.isEdit && !data.password) return true
      return data.password.length > 0
    },
    { message: "비밀번호를 입력하세요.", path: ["password"] }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return password.length >= 8
    },
    {
      message: "비밀번호는 8자 이상이어야 합니다.",
      path: ["password"],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return /[a-z]/.test(password)
    },
    {
      message: "비밀번호에 소문자를 1자 이상 포함해야 합니다.",
      path: ["password"],
    }
  )
  .refine(
    ({ isEdit, password }) => {
      if (isEdit && !password) return true
      return /\d/.test(password)
    },
    {
      message: "비밀번호에 숫자를 1자 이상 포함해야 합니다.",
      path: ["password"],
    }
  )
  .refine(
    ({ isEdit, password, confirmPassword }) => {
      if (isEdit && !password) return true
      return password === confirmPassword
    },
    { message: "비밀번호가 일치하지 않습니다.", path: ["confirmPassword"] }
  )
  .refine(
    ({ isSelf, password, currentPassword }) => {
      if (!isSelf || !password) return true
      return currentPassword.length > 0
    },
    { message: "현재 비밀번호를 입력하세요.", path: ["currentPassword"] }
  )
type UserForm = z.infer<typeof formSchema>

/**
 * Helper component that renders a form label with a red asterisk
 * to indicate the field is required.
 */
function RequiredLabel({ children }: { children: React.ReactNode }) {
  return (
    <FormLabel>
      {children} <span className="text-destructive">*</span>
    </FormLabel>
  )
}

type UsersActionDialogProps = {
  /** The user being edited, or undefined for adding a new user. */
  currentRow?: User
  /** Whether the dialog is open. */
  open: boolean
  /** Callback to open or close the dialog. */
  onOpenChange: (open: boolean) => void
  /** Hide the Role field (used for Account Settings self-edit). */
  hideRole?: boolean
  /** Custom onSave callback (used for Account Settings without UsersProvider). */
  onSaved?: () => void
}

export function UsersActionDialog({
  currentRow,
  open,
  onOpenChange,
  hideRole = false,
  onSaved,
}: UsersActionDialogProps) {
  const isEdit = !!currentRow
  const usersCtx = useUsersOptional()
  const refreshUsers = usersCtx?.refreshUsers

  /**
   * Initialize the form with React Hook Form + Zod resolver.
   *
   * In edit mode, the form is pre-populated with the current user's data
   * (password fields start empty). In add mode, all fields start empty
   * with the default role set to "user".
   *
   * Validation runs on touched fields (`mode: "onTouched"`) to provide
   * immediate feedback without validating untouched fields.
   */
  const form = useForm<UserForm>({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    resolver: zodResolver(formSchema) as any,
    mode: "onTouched",
    defaultValues: isEdit
      ? {
          ...currentRow,
          organization: currentRow?.organization ?? "",
          department: currentRow?.department ?? "",
          password: "",
          confirmPassword: "",
          currentPassword: "",
          isEdit,
          isSelf: hideRole,
        }
      : {
          firstName: "",
          lastName: "",
          username: "",
          email: "",
          organization: "",
          department: "",
          role: "argus-user",
          phoneNumber: "",
          password: "",
          confirmPassword: "",
          currentPassword: "",
          isEdit,
          isSelf: false,
        },
  })

  /**
   * Async blur handler for the username field.
   *
   * When the user tabs out of the username input (in add mode only),
   * this calls the backend to check if the username is already taken.
   * If it is, a form error is set on the username field.
   */
  const handleCheckUsername = useCallback(
    async (value: string) => {
      if (!value || isEdit) return
      try {
        const result = await checkUserExists({ username: value })
        if (result.username_exists) {
          form.setError("username", {
            type: "validate",
            message: "이미 사용 중인 사용자명입니다.",
          })
        }
      } catch {
        // ignore network errors for validation
      }
    },
    [isEdit, form]
  )

  /**
   * Async blur handler for the email field.
   *
   * Similar to username check — validates email uniqueness against the backend
   * when the user tabs out of the email input. Only runs in add mode.
   */
  const handleCheckEmail = useCallback(
    async (value: string) => {
      if (!value || isEdit) return
      try {
        const result = await checkUserExists({ email: value })
        if (result.email_exists) {
          form.setError("email", {
            type: "validate",
            message: "이미 등록된 이메일입니다.",
          })
        }
      } catch {
        // ignore network errors for validation
      }
    },
    [isEdit, form]
  )

  /**
   * Form submission handler.
   *
   * In edit mode: calls `modifyUser()` with only the editable profile fields.
   * In add mode: calls `createUser()` with all fields including password.
   * The role value is mapped from lowercase ("admin") to title-case ("Admin")
   * to match the backend's RoleName enum.
   *
   * After a successful save, refreshes the user list and closes the dialog.
   */
  const onSubmit = async (values: UserForm) => {
    try {
      if (isEdit && currentRow) {
        // 계정 설정(본인)에서 새 비밀번호를 입력한 경우 — 현재 비밀번호 검증을
        // 먼저 수행해, 틀렸으면 프로필 저장 전에 중단한다.
        if (hideRole && values.password) {
          try {
            await changeMyPassword(values.currentPassword, values.password)
          } catch (err) {
            form.setError("currentPassword", {
              type: "validate",
              message: err instanceof Error ? err.message : "비밀번호 변경에 실패했습니다.",
            })
            return
          }
        }
        await modifyUser(currentRow.id, {
          first_name: values.firstName,
          last_name: values.lastName,
          email: values.email,
          organization: values.organization,
          department: values.department,
          phone_number: values.phoneNumber,
        })
        // Change role if it was modified (skip in hideRole mode)
        if (!hideRole && values.role && values.role !== currentRow.role) {
          await changeUserRole(currentRow.id, values.role)
        }
      } else {
        // 추가 모드: 저장 직전 사용자명·이메일 중복을 한 번 더 확인.
        // blur 핸들러가 실행되기 전(붙여넣기 후 즉시 Enter 등)이나 blur 시점에
        // 네트워크 오류가 있었던 경우를 대비한 마지막 방어선이다.
        try {
          const check = await checkUserExists({
            username: values.username,
            email: values.email,
          })
          let hasDuplicate = false
          if (check.username_exists) {
            form.setError("username", {
              type: "validate",
              message: "이미 사용 중인 사용자명입니다.",
            })
            hasDuplicate = true
          }
          if (check.email_exists) {
            form.setError("email", {
              type: "validate",
              message: "이미 등록된 이메일입니다.",
            })
            hasDuplicate = true
          }
          if (hasDuplicate) return
        } catch {
          // 중복 확인 자체가 실패하면 저장을 막아 데이터 무결성을 우선한다.
          toast.error("사용자명·이메일 중복 확인에 실패했습니다. 다시 시도해주세요.")
          return
        }

        await createUser({
          username: values.username,
          email: values.email,
          first_name: values.firstName,
          last_name: values.lastName,
          organization: values.organization,
          department: values.department,
          phone_number: values.phoneNumber,
          password: values.password,
          role: values.role,
        })
      }
      if (refreshUsers) await refreshUsers()
      if (onSaved) onSaved()
      form.reset()
      onOpenChange(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "사용자 저장에 실패했습니다."
      toast.error(msg)
    }
  }

  const isPasswordTouched = !!form.formState.dirtyFields.password

  /**
   * Compute whether the Save button should be enabled.
   *
   * The button is disabled when:
   * - Required fields are not filled in.
   * - There are any validation errors (from Zod or async checks).
   *
   * In edit mode, only firstName, lastName, and email are required.
   * In add mode, all fields including username, role, password, and
   * confirmPassword are required.
   */
  const watched = form.watch()
  const hasErrors = Object.keys(form.formState.errors).length > 0
  const requiredFilled = isEdit
    ? !!(watched.firstName && watched.lastName && watched.email)
    : !!(
        watched.firstName &&
        watched.lastName &&
        watched.username &&
        watched.email &&
        watched.role &&
        watched.password &&
        watched.confirmPassword
      )
  const isSaveDisabled = !requiredFilled || hasErrors

  return (
    <Dialog
      open={open}
      onOpenChange={(state) => {
        form.reset()
        onOpenChange(state)
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader className="text-start">
          <DialogTitle>{isEdit ? "사용자 정보 수정" : "사용자 추가"}</DialogTitle>
          <DialogDescription>
            {isEdit ? "사용자 정보를 수정합니다. " : "새 사용자를 등록합니다. "}
            완료되면 저장 버튼을 누르세요.
          </DialogDescription>
        </DialogHeader>
        <div className="py-1">
          <Form {...form}>
            <form
              id="user-form"
              onSubmit={form.handleSubmit(onSubmit)}
              className="space-y-4"
            >
              {/* First Name and Last Name side by side */}
              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="firstName"
                  render={({ field }) => (
                    <FormItem>
                      <RequiredLabel>이름</RequiredLabel>
                      <FormControl>
                        <Input placeholder="John" autoComplete="off" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="lastName"
                  render={({ field }) => (
                    <FormItem>
                      <RequiredLabel>성</RequiredLabel>
                      <FormControl>
                        <Input placeholder="Doe" autoComplete="off" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              {/* Username field — disabled in edit mode since it cannot be changed */}
              <FormField
                control={form.control}
                name="username"
                render={({ field }) => (
                  <FormItem>
                    <RequiredLabel>사용자명</RequiredLabel>
                    <FormControl>
                      <Input
                        placeholder="john_doe"
                        disabled={isEdit}
                        {...field}
                        onBlur={(e) => {
                          field.onBlur()
                          handleCheckUsername(e.target.value)
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* 소속 / 소속 부서 — 사용자명 아래 */}
              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="organization"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>소속</FormLabel>
                      <FormControl>
                        <Input placeholder="예: 데이터다이나믹스" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="department"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>소속 부서</FormLabel>
                      <FormControl>
                        <Input placeholder="예: 데이터 플랫폼팀" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              {/* Email field — with async uniqueness check on blur */}
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <RequiredLabel>이메일</RequiredLabel>
                    <FormControl>
                      <Input
                        placeholder="john.doe@gmail.com"
                        {...field}
                        onBlur={(e) => {
                          field.onBlur()
                          handleCheckEmail(e.target.value)
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Phone Number and Role side by side */}
              <div className={hideRole ? "" : "grid grid-cols-2 gap-4"}>
                <FormField
                  control={form.control}
                  name="phoneNumber"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>전화번호</FormLabel>
                      <FormControl>
                        <Input placeholder="+123456789" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {!hideRole && (
                  <FormField
                    control={form.control}
                    name="role"
                    render={({ field }) => (
                      <FormItem>
                        <RequiredLabel>역할</RequiredLabel>
                        <SelectDropdown
                          defaultValue={field.value}
                          onValueChange={field.onChange}
                          placeholder="역할 선택"
                          className="w-full"
                          items={roles.map(({ label, value }) => ({ label, value }))}
                        />
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
              </div>

              {/* 비밀번호 — 추가 모드(필수)와 계정 설정(본인, 선택)에서만 표시.
                  관리자 편집 모드에서는 비밀번호를 변경할 수 없어 숨긴다. */}
              {hideRole && isEdit && (
                <FormField
                  control={form.control}
                  name="currentPassword"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>현재 비밀번호</FormLabel>
                      <FormControl>
                        <PasswordInput placeholder="비밀번호 변경 시에만 입력" autoComplete="current-password" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              {(!isEdit || hideRole) && (
                <>
                  <FormField
                    control={form.control}
                    name="password"
                    render={({ field }) => (
                      <FormItem>
                        {isEdit ? (
                          <FormLabel>새 비밀번호</FormLabel>
                        ) : (
                          <RequiredLabel>비밀번호</RequiredLabel>
                        )}
                        <FormControl>
                          <PasswordInput defaultVisible placeholder="e.g., S3cur3P@ssw0rd" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {/* Confirm Password — only enabled after the password field is touched */}
                  <FormField
                    control={form.control}
                    name="confirmPassword"
                    render={({ field }) => (
                      <FormItem>
                        {isEdit ? (
                          <FormLabel>새 비밀번호 확인</FormLabel>
                        ) : (
                          <RequiredLabel>비밀번호 확인</RequiredLabel>
                        )}
                        <FormControl>
                          <PasswordInput
                            defaultVisible
                            disabled={!isPasswordTouched}
                            placeholder="e.g., S3cur3P@ssw0rd"
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              )}
            </form>
          </Form>
        </div>
        <DialogFooter>
          <Button type="submit" form="user-form" disabled={isSaveDisabled}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
