import "@workspace/ui/globals.css"
// 댓글 에디터·뷰어의 코드블록 syntax highlight (lowlight + highlight.js) 테마.
// .hljs / .hljs-keyword 등의 토큰 색상 + ``<pre class="hljs">`` 컨테이너 보강 룰.
// 한 번만 import 하면 editor·viewer 양쪽 전역 적용.
import "@/components/comments/highlight-styles.css"
import type { Metadata } from "next"
import localFont from "next/font/local"
import { TooltipProvider } from "@workspace/ui/components/tooltip"
import { NotifyToaster } from "@/components/notify-toaster"
import { ConfirmProvider } from "@/components/confirm-provider"
import { ThemeProvider } from "@/components/theme-provider"
import { AuthProviderWrapper } from "@/components/auth-provider-wrapper" // Added for SSO AUTH
import { brandTitle } from "@/lib/branding-server"

const pretendard = localFont({
  src: "./fonts/PretendardVariable.woff2",
  variable: "--font-pretendard",
  display: "swap",
  weight: "45 920",
})

const notoSansKR = localFont({
  src: [
    { path: "./fonts/NotoSansKR-400.woff2", weight: "400", style: "normal" },
    { path: "./fonts/NotoSansKR-500.woff2", weight: "500", style: "normal" },
    { path: "./fonts/NotoSansKR-700.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-noto-sans-kr",
  display: "swap",
})

const robotoCondensed = localFont({
  src: "./fonts/RobotoCondensed-Variable.woff2",
  variable: "--font-roboto-condensed",
  display: "swap",
  weight: "100 900",
})

const d2coding = localFont({
  src: [
    { path: "./fonts/D2Coding-Regular.woff2", weight: "400", style: "normal" },
    { path: "./fonts/D2Coding-Bold.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-d2coding",
  display: "swap",
})

// 브라우저 탭 제목 = 설정 > 사이드바 표시의 제품명(TODO-147)
export async function generateMetadata(): Promise<Metadata> {
  return { title: await brandTitle() }
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="ko"
      suppressHydrationWarning
      className={`${pretendard.variable} ${notoSansKR.variable} ${robotoCondensed.variable} ${d2coding.variable}`}
    >
      <body className="min-h-screen bg-background text-foreground text-sm font-sans antialiased">
        <ThemeProvider>
          {/* Added for SSO AUTH - wraps entire app with authentication context */}
          {/* 툴팁 대기 시간은 앱 전체에서 하나(TODO-120) — 화면별 TooltipProvider 를 두지 않는다 */}
          <TooltipProvider delayDuration={300}>
            <ConfirmProvider>
              <AuthProviderWrapper>
                {children}
              </AuthProviderWrapper>
            </ConfirmProvider>
          </TooltipProvider>
          <NotifyToaster />
        </ThemeProvider>
      </body>
    </html>
  )
}
