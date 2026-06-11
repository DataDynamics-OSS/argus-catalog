/**
 * 이미지 클라이언트 측 압축.
 *
 * TipTap 본문에 paste/drop 한 이미지가 base64 로 HTML 안에 인라인 저장되므로
 * 원본을 그대로 임베드하면 댓글 한 건의 body 가 수 MB 까지 커진다. 사용자가
 * paste 하는 시점에 한 번 리사이즈 + 재압축하면 일반적으로 5MB → ~200KB 수준까지
 * 줄어든다.
 *
 * 정책:
 * - 최장 변이 maxDim 을 넘으면 비율 유지 축소
 * - 충분히 작은 (<100KB) 원본은 손실을 피하기 위해 그대로 보존
 * - 이미지가 아니면 그대로 data URL 만들어 반환
 * - 출력 포맷은 JPEG (alpha 채널이 필요 없는 일반 스크린샷용). PNG 가
 *   필요하다면 호출자가 mime 을 "image/png" 로 지정.
 */

const PASSTHROUGH_BYTES = 100 * 1024 // 100KB 미만은 압축 생략

type Options = {
  maxDim?: number
  quality?: number
  mime?: string
}

export async function compressImageToDataUrl(
  file: File,
  opts: Options = {},
): Promise<string> {
  const { maxDim = 1600, quality = 0.85, mime = "image/jpeg" } = opts
  if (!file.type.startsWith("image/")) {
    return readAsDataUrl(file)
  }

  let img: HTMLImageElement
  let objectUrl: string | null = null
  try {
    objectUrl = URL.createObjectURL(file)
    img = await loadImage(objectUrl)
  } catch {
    return readAsDataUrl(file)
  }

  try {
    const ratio = Math.min(maxDim / img.width, maxDim / img.height, 1)
    // 작은 원본 + 축소 불필요 → 원본 base64 보존
    if (ratio === 1 && file.size < PASSTHROUGH_BYTES) {
      return readAsDataUrl(file)
    }
    const w = Math.max(1, Math.round(img.width * ratio))
    const h = Math.max(1, Math.round(img.height * ratio))
    const canvas = document.createElement("canvas")
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext("2d")
    if (!ctx) return readAsDataUrl(file)
    // JPEG 출력 시 흰 배경 — 투명 PNG 의 알파를 검정이 아닌 흰색으로 보정
    if (mime === "image/jpeg") {
      ctx.fillStyle = "#ffffff"
      ctx.fillRect(0, 0, w, h)
    }
    ctx.drawImage(img, 0, 0, w, h)
    return canvas.toDataURL(mime, quality)
  } finally {
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const i = new Image()
    i.onload = () => resolve(i)
    i.onerror = reject
    i.src = src
  })
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
