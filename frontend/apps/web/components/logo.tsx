import * as React from "react"

/**
 * Argus Catalog 로고 마크 — "Data-Node Eye".
 *
 * 육각형(데이터 노드/그래프 = 카탈로그·리니지) 안에 눈(Argus = 그리스 신화의 전수감시자,
 * "모든 데이터를 본다")을 결합한 마크. `currentColor` 를 사용하므로 텍스트 색상에 맞춰
 * 라이트/다크에서 자동으로 대응한다. 16~32px 초소형에서도 또렷하도록 단순 기하 + 굵은 stroke.
 */
export function Logo({ className, ...props }: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
      {...props}
    >
      {/* 데이터 노드(육각형) */}
      <path d="M12 2.5 20.4 7v10L12 21.5 3.6 17V7L12 2.5Z" />
      {/* 홍채(눈) */}
      <circle cx="12" cy="12" r="3.4" />
      {/* 동공 */}
      <circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" />
    </svg>
  )
}
