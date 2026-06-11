"use client"

import { useEffect, useState } from "react"
import Highcharts from "highcharts"
import HighchartsReact from "highcharts-react-official"

/**
 * SSR 안전한 Highcharts 래퍼.
 *
 * Highcharts 는 mount 시 ``window`` 를 접근하므로, Next.js 의 첫 server 렌더 패스에서는
 * 빈 placeholder 만 그려 두고 client 에서 hydrate 된 뒤 실제 차트를 그리도록 한다.
 */
type HighchartProps = {
  options: Highcharts.Options
  /** 컨테이너 높이(px). 차트 자체 높이는 ``options.chart.height`` 로도 지정 가능. */
  height?: number
  className?: string
}

export function Highchart({ options, height, className }: HighchartProps) {
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  // 차트 높이는 prop > options.chart.height > 기본값 순으로 결정
  // tooltip 은 헤더(시리즈명·x값)와 본문 사이의 폰트 크기/패밀리 차이를 없애기 위해
  // 공통 기본값을 주입하고, 차트별 tooltip 옵션이 있으면 그것이 우선한다.
  const merged: Highcharts.Options = {
    credits: { enabled: false },
    accessibility: { enabled: false },
    ...options,
    chart: {
      backgroundColor: "transparent",
      height,
      ...options.chart,
    },
    tooltip: {
      headerFormat: "<b>{point.key}</b><br/>",
      style: { fontSize: "12px", fontFamily: "inherit" },
      ...options.tooltip,
    },
  }

  if (!mounted) {
    // 서버 측 렌더에서는 동일 높이의 placeholder 만 그려 layout shift 방지
    return <div className={className} style={{ height }} />
  }

  return (
    <div className={className}>
      <HighchartsReact highcharts={Highcharts} options={merged} />
    </div>
  )
}
