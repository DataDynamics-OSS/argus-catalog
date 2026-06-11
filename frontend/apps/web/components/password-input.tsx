"use client"

import * as React from "react"
import { Eye, EyeOff } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { Button } from "@workspace/ui/components/button"

type PasswordInputProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "type"
> & {
  ref?: React.Ref<HTMLInputElement>
  /** true 면 기본적으로 평문으로 표시(eye 로 숨김 토글). 기본 false(마스킹). */
  defaultVisible?: boolean
}

export function PasswordInput({
  className,
  disabled,
  ref,
  defaultVisible = false,
  ...props
}: PasswordInputProps) {
  const [showPassword, setShowPassword] = React.useState(defaultVisible)

  return (
    <div className={cn("relative rounded-md", className)}>
      <input
        type={showPassword ? "text" : "password"}
        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-hidden disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40"
        ref={ref}
        disabled={disabled}
        {...props}
      />
      <Button
        type="button"
        size="icon"
        variant="ghost"
        disabled={disabled}
        className="absolute end-1 top-1/2 h-6 w-6 -translate-y-1/2 rounded-md text-muted-foreground"
        onClick={() => setShowPassword((prev) => !prev)}
      >
        {showPassword ? <Eye size={18} /> : <EyeOff size={18} />}
      </Button>
    </div>
  )
}
