import { fileURLToPath } from "node:url"
import { defineConfig } from "vitest/config"

// 순수 로직(메뉴 병합·권한 판단 등) 단위 테스트용. 컴포넌트 렌더 테스트는 아직 없다.
export default defineConfig({
  resolve: {
    alias: {
      "@/": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    include: ["**/*.test.ts"],
    exclude: ["node_modules/**", ".next/**"],
  },
})
