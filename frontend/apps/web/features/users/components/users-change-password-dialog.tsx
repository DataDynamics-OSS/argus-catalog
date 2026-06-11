/**
 * 관리자용 비밀번호 변경 다이얼로그.
 *
 * 사용자 관리 테이블의 행 메뉴("...")에서 "비밀번호 변경"을 누르면 열린다. 관리자가 대상
 * 사용자의 비밀번호를 직접 재설정하므로 현재 비밀번호는 요구하지 않는다(본인 변경과 다름).
 *
 * 검증 규칙(users-action-dialog 와 동일): 8자 이상, 소문자 1자 이상, 숫자 1자 이상,
 * 새 비밀번호와 확인 일치. 성공 시 `setUserPassword` 호출 → 토스트 → 목록 새로고침.
 */

"use client"

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
import { PasswordInput } from "@/components/password-input"
import { setUserPassword } from "../api"
import { type User } from "../data/schema"
import { useUsersOptional } from "./users-provider"

const formSchema = z
  .object({
    password: z.string().transform((pwd) => pwd.trim()),
    confirmPassword: z.string().transform((pwd) => pwd.trim()),
  })
  .refine((data) => data.password.length >= 8, {
    message: "비밀번호는 8자 이상이어야 합니다.",
    path: ["password"],
  })
  .refine((data) => /[a-z]/.test(data.password), {
    message: "비밀번호에 소문자를 1자 이상 포함해야 합니다.",
    path: ["password"],
  })
  .refine((data) => /\d/.test(data.password), {
    message: "비밀번호에 숫자를 1자 이상 포함해야 합니다.",
    path: ["password"],
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "비밀번호가 일치하지 않습니다.",
    path: ["confirmPassword"],
  })
type ChangePasswordForm = z.infer<typeof formSchema>

type UsersChangePasswordDialogProps = {
  /** 비밀번호를 변경할 대상 사용자. */
  currentRow: User
  /** 다이얼로그 열림 여부. */
  open: boolean
  /** 다이얼로그 열기/닫기 콜백. */
  onOpenChange: (open: boolean) => void
}

export function UsersChangePasswordDialog({
  currentRow,
  open,
  onOpenChange,
}: UsersChangePasswordDialogProps) {
  const refreshUsers = useUsersOptional()?.refreshUsers

  const form = useForm<ChangePasswordForm>({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    resolver: zodResolver(formSchema) as any,
    mode: "onTouched",
    defaultValues: { password: "", confirmPassword: "" },
  })

  const onSubmit = async (values: ChangePasswordForm) => {
    try {
      await setUserPassword(currentRow.id, values.password)
      if (refreshUsers) await refreshUsers()
      toast.success(`${currentRow.username} 님의 비밀번호를 변경했습니다.`)
      form.reset()
      onOpenChange(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "비밀번호 변경에 실패했습니다."
      toast.error(msg)
    }
  }

  const isPasswordTouched = !!form.formState.dirtyFields.password
  const watched = form.watch()
  const hasErrors = Object.keys(form.formState.errors).length > 0
  const isSaveDisabled = !watched.password || !watched.confirmPassword || hasErrors

  return (
    <Dialog
      open={open}
      onOpenChange={(state) => {
        form.reset()
        onOpenChange(state)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader className="text-start">
          <DialogTitle>비밀번호 변경</DialogTitle>
          <DialogDescription>
            <span className="font-medium">{currentRow.username}</span> 님의 새 비밀번호를 설정합니다.
            완료되면 저장 버튼을 누르세요.
          </DialogDescription>
        </DialogHeader>
        <div className="py-1">
          <Form {...form}>
            <form
              id="change-password-form"
              onSubmit={form.handleSubmit(onSubmit)}
              className="space-y-4"
            >
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      새 비밀번호 <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <PasswordInput
                        defaultVisible
                        placeholder="e.g., S3cur3P@ssw0rd"
                        autoComplete="new-password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="confirmPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      새 비밀번호 확인 <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <PasswordInput
                        defaultVisible
                        disabled={!isPasswordTouched}
                        placeholder="e.g., S3cur3P@ssw0rd"
                        autoComplete="new-password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </form>
          </Form>
        </div>
        <DialogFooter>
          <Button type="submit" form="change-password-form" disabled={isSaveDisabled}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
