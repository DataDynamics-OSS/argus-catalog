import "@workspace/ui/globals.css"
// 댓글 에디터·뷰어의 코드블록 syntax highlight (lowlight + highlight.js) 테마.
// .hljs / .hljs-keyword 등의 토큰 색상 + ``<pre class="hljs">`` 컨테이너 보강 룰.
// 한 번만 import 하면 editor·viewer 양쪽 전역 적용.
import "@/components/comments/highlight-styles.css"
import localFont from "next/font/local"
import { Toaster } from "sonner"
import { ThemeProvider } from "@/components/theme-provider"
import { AuthProviderWrapper } from "@/components/auth-provider-wrapper" // Added for SSO AUTH

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
          <AuthProviderWrapper>
            {children}
          </AuthProviderWrapper>
          <Toaster richColors position="top-center" />
        </ThemeProvider>
      </body>
    </html>
  )
}
