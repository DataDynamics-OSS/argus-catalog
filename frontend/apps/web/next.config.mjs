import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))

/** @type {import('next').NextConfig} */
const nextConfig = {
  // 릴리스 패키지(release/)는 NEXT_OUTPUT=standalone 으로 빌드해 node 하나로 실행되는 묶음(server.js)을 만든다.
  // 기본(next start·docker 이미지)은 기존과 같다. 모노레포라 추적 루트를 frontend/ 로 둔다.
  ...(process.env.NEXT_OUTPUT === "standalone"
    ? { output: "standalone", outputFileTracingRoot: path.join(here, "../..") }
    : {}),
  transpilePackages: ["@workspace/ui"],
  logging: {
    fetches: {
      fullUrl: true,
    },
  },
  // 개발 서버 cross-origin 허용 — 환경변수로 오버라이드 가능.
  // 기본값은 localhost 와 사내망 대역(10.0.1.*, 192.168.0.*)을 와일드카드로 허용한다.
  allowedDevOrigins: process.env.ALLOWED_DEV_ORIGINS
    ? process.env.ALLOWED_DEV_ORIGINS.split(",")
    : ["localhost", "10.0.1.*", "192.168.0.*"],
  // 도움말 매뉴얼(public/manual, Antora 빌드)의 루트 — public/ 은 디렉터리 주소에 index.html 을 붙여 주지 않아
  // /manual · /manual/ 이 404 였다(DEF-DOC-001). 시작 페이지로 보낸다. 매뉴얼 안 링크는 머리글 partial 에서 고쳤다.
  async redirects() {
    return [
      { source: "/manual", destination: "/manual/argus-catalog/index.html", permanent: false },
    ]
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Access-Control-Allow-Origin", value: "*" },
          { key: "Access-Control-Allow-Methods", value: "GET, POST, PUT, PATCH, DELETE, OPTIONS" },
          { key: "Access-Control-Allow-Headers", value: "Content-Type, Authorization" },
        ],
      },
    ]
  },
}

export default nextConfig
