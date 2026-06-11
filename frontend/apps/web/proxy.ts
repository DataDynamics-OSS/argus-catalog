import { type NextRequest, NextResponse } from "next/server"

const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:4600"

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl

  // Next 16 의 새 proxy 컨벤션에서 ``export const config.matcher`` 가 모든 경로(``/:path*``)
  // 로 등록되는 현상이 있어, 백엔드로 보낼 경로(``/api/v1/*``)가 아니면 다음 핸들러로 넘긴다.
  if (!pathname.startsWith("/api/v1/")) {
    return NextResponse.next()
  }

  const destination = `${API_BASE_URL}${pathname}${search}`

  try {
    const backendResponse = await fetch(destination, {
      method: request.method,
      headers: request.headers,
      body: request.method !== "GET" && request.method !== "HEAD" ? request.body : undefined,
      // @ts-expect-error -- Next.js supports duplex for streaming request bodies
      duplex: "half",
    })

    const responseHeaders = new Headers(backendResponse.headers)
    responseHeaders.delete("transfer-encoding")

    return new NextResponse(backendResponse.body, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    })
  } catch {
    return NextResponse.json(
      { detail: "Backend server is unreachable. Please check if catalog-server is running." },
      { status: 502 },
    )
  }
}

export const config = {
  matcher: "/api/v1/:path*",
}
