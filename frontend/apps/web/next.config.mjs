/** @type {import('next').NextConfig} */
const nextConfig = {
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
