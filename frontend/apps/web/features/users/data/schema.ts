import { z } from "zod"

/**
 * User account status enum schema.
 *
 * Defines the possible states of a user account:
 * - active:   The user can log in and use the datasource normally.
 * - inactive: The user account has been deactivated by an admin and cannot log in.
 */
const userStatusSchema = z.union([
  z.literal("active"),
  z.literal("inactive"),
])
export type UserStatus = z.infer<typeof userStatusSchema>

/**
 * User role enum schema.
 *
 * Defines the authorization level of a user:
 * - admin: Full datasource access including user management and configuration.
 * - user:  Standard access with limited administrative capabilities.
 */
const userRoleSchema = z.union([
  z.literal("argus-admin"),
  z.literal("argus-superuser"),
  z.literal("argus-user"),
])

/**
 * User schema.
 *
 * Represents a single user record as displayed in the UI.
 * Fields use camelCase on the frontend (converted from the backend's snake_case
 * via the `mapUser` function in `api.ts`).
 */
const userSchema = z.object({
  /** Unique numeric identifier for the user (auto-incremented by the database). */
  id: z.string(),
  /** User's first (given) name. */
  firstName: z.string(),
  /** User's last (family) name. */
  lastName: z.string(),
  /** Unique login identifier chosen by the user. Cannot be changed after creation. */
  username: z.string(),
  /** User's email address. Must be unique across all users. */
  email: z.string(),
  /** 소속(회사/조직). 선택. */
  organization: z.string().optional(),
  /** 소속 부서. 선택. */
  department: z.string().optional(),
  /** Optional contact phone number (e.g. "+82-10-1234-5678"). */
  phoneNumber: z.string(),
  /** Current account status (active or inactive). */
  status: userStatusSchema,
  /** Assigned role determining the user's permission level. */
  role: userRoleSchema,
  /** Timestamp when the user account was created. */
  createdAt: z.coerce.date(),
  /** Timestamp of the most recent profile update. */
  updatedAt: z.coerce.date(),
})
export type User = z.infer<typeof userSchema>

/** Zod schema for validating an array of User objects. */
export const userListSchema = z.array(userSchema)
