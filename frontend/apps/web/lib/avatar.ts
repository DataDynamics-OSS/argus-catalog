"use client"

import { Avatar, Style } from "@dicebear/core"
import adventurerDefinition from "@dicebear/styles/adventurer.json"

export type AvatarPreset = {
  id: string
  label: string
  dataUri: string
}

// dicebear v10: 스타일은 JSON 정의를 Style 로 감싸서 사용한다.
const adventurer = new Style(adventurerDefinition)

function buildPreset(id: string, label: string, style: Style, seed: string): AvatarPreset {
  return {
    id,
    label,
    dataUri: new Avatar(style, { seed }).toDataUri(),
  }
}

// 10 seeds shown in the DiceBear playground when the "adventurer" style is
// selected (https://www.dicebear.com/playground/).
export const AVATAR_PRESETS: AvatarPreset[] = [
  buildPreset("adventurer-aiden", "Aiden", adventurer, "aiden"),
  buildPreset("adventurer-aidan", "Aidan", adventurer, "aidan"),
  buildPreset("adventurer-maria", "Maria", adventurer, "maria"),
  buildPreset("adventurer-lliam", "Lliam", adventurer, "lliam"),
  buildPreset("adventurer-oliver", "Oliver", adventurer, "oliver"),
  buildPreset("adventurer-eliza", "Eliza", adventurer, "eliza"),
  buildPreset("adventurer-vivian", "Vivian", adventurer, "vivian"),
  buildPreset("adventurer-ryker", "Ryker", adventurer, "ryker"),
  buildPreset("adventurer-sophia", "Sophia", adventurer, "sophia"),
  buildPreset("adventurer-avery", "Avery", adventurer, "avery"),
]

export function findPreset(presetId: string | null | undefined): AvatarPreset | null {
  if (!presetId) return null
  return AVATAR_PRESETS.find((p) => p.id === presetId) ?? null
}
