/**
 * TipTap 이모지·특수기호 picker 카탈로그 — 큐레이션.
 *
 * 사내 도구용 자주 쓰이는 항목만 추렸다 (총 약 200). 풀 emoji set 이 필요한
 * 케이스는 OS native picker (⌃⌘Space / Win+.) 로 보완.
 *
 * 각 항목 = { char, name(한글 tooltip + 검색), kw?(추가 영문 키워드) }
 *   - name 은 hover tooltip 으로도 사용. 명료한 한글 명사 권장.
 *   - kw 는 검색 hit rate 높이는 영문/유사어 — "smile, happy, joy".
 *
 * '자주' 카테고리는 localStorage 에 최근 사용 12개를 stack 으로 저장.
 */

export type EmojiItem = {
  char: string;
  name: string;
  kw?: string;
};

export type EmojiCategory = {
  key: string;
  label: string;
  items: EmojiItem[];
};

// 표정·반응 — 회의·코멘트에서 자주 등장하는 액션 / 평가 / 감정 묶음.
const FACE: EmojiItem[] = [
  // 체크·표시
  { char: "✅", name: "체크", kw: "check ok done" },
  { char: "❌", name: "엑스", kw: "x cancel no" },
  { char: "⚠️", name: "경고", kw: "warning warn" },
  { char: "❓", name: "물음표", kw: "question" },
  { char: "❗", name: "느낌표", kw: "exclamation" },
  { char: "🚨", name: "사이렌", kw: "alert siren urgent" },
  { char: "🔴", name: "빨간원", kw: "red dot critical" },
  { char: "🟠", name: "주황원", kw: "orange dot" },
  { char: "🟡", name: "노란원", kw: "yellow dot" },
  { char: "🟢", name: "초록원", kw: "green dot ok" },
  { char: "🔵", name: "파란원", kw: "blue dot" },
  { char: "⚫", name: "검은원", kw: "black dot" },
  // 작업·아이디어
  { char: "🎯", name: "표적", kw: "target goal" },
  { char: "🔥", name: "불", kw: "fire hot" },
  { char: "🚀", name: "로켓", kw: "rocket launch" },
  { char: "⭐", name: "별", kw: "star" },
  { char: "💡", name: "아이디어", kw: "idea bulb" },
  { char: "📌", name: "핀", kw: "pin" },
  { char: "✏️", name: "연필", kw: "edit pencil" },
  { char: "📅", name: "달력", kw: "calendar date" },
  { char: "📊", name: "차트", kw: "chart" },
  { char: "📝", name: "메모", kw: "memo note" },
  { char: "📎", name: "클립", kw: "attachment paperclip" },
  { char: "🔗", name: "링크", kw: "link" },
  // 액션·반응
  { char: "🎉", name: "축하", kw: "party congrats" },
  { char: "👍", name: "좋아요", kw: "thumbs up like" },
  { char: "👎", name: "싫어요", kw: "thumbs down" },
  { char: "👏", name: "박수", kw: "clap" },
  { char: "🙌", name: "만세", kw: "raise hands" },
  { char: "🙏", name: "감사", kw: "thanks please pray" },
  { char: "✔️", name: "확인", kw: "check tick" },
  { char: "✖️", name: "취소", kw: "x cross" },
  { char: "➕", name: "추가", kw: "plus add" },
  { char: "➖", name: "빼기", kw: "minus" },
  // 표정
  { char: "😀", name: "웃음", kw: "smile happy" },
  { char: "😃", name: "활짝", kw: "smile" },
  { char: "😄", name: "기쁨", kw: "smile" },
  { char: "😁", name: "활짝웃음", kw: "grin" },
  { char: "😅", name: "땀웃음", kw: "sweat smile" },
  { char: "😂", name: "눈물웃음", kw: "joy tears" },
  { char: "😊", name: "미소", kw: "smile blush" },
  { char: "🙂", name: "약한미소", kw: "slight smile" },
  { char: "😉", name: "윙크", kw: "wink" },
  { char: "😎", name: "쿨", kw: "cool sunglasses" },
  { char: "🤔", name: "생각", kw: "thinking" },
  { char: "😐", name: "무표정", kw: "neutral" },
  { char: "😑", name: "무관심", kw: "expressionless" },
  { char: "😴", name: "잠", kw: "sleep" },
  // 부정·강조 감정
  { char: "😢", name: "눈물", kw: "cry sad" },
  { char: "😭", name: "통곡", kw: "cry loud" },
  { char: "😤", name: "분노콧김", kw: "huff" },
  { char: "😠", name: "화남", kw: "angry" },
  { char: "😱", name: "비명", kw: "scream shocked" },
  { char: "🤯", name: "폭발", kw: "mind blown" },
  { char: "🥲", name: "슬픈웃음", kw: "smile tear" },
  { char: "🥳", name: "파티", kw: "party hat" },
  { char: "🤝", name: "악수", kw: "handshake deal" },
  { char: "💼", name: "서류가방", kw: "briefcase work" },
  { char: "🏢", name: "회사", kw: "office building" },
  { char: "💬", name: "말풍선", kw: "speech bubble" },
  { char: "💭", name: "생각풍선", kw: "thought" },
];

// 일반 기호 — 글머리·강조·구두점 등.
const SYM: EmojiItem[] = [
  { char: "•", name: "점", kw: "bullet" },
  { char: "·", name: "가운뎃점", kw: "middle dot" },
  { char: "●", name: "큰점", kw: "circle filled" },
  { char: "○", name: "빈동그라미", kw: "circle empty" },
  { char: "■", name: "사각", kw: "square filled" },
  { char: "□", name: "빈사각", kw: "square empty" },
  { char: "▪", name: "작은사각", kw: "small square" },
  { char: "▫", name: "작은빈사각", kw: "small empty square" },
  { char: "◆", name: "다이아몬드", kw: "diamond" },
  { char: "◇", name: "빈다이아", kw: "diamond empty" },
  { char: "▲", name: "삼각", kw: "triangle up" },
  { char: "▼", name: "역삼각", kw: "triangle down" },
  { char: "★", name: "별", kw: "star solid" },
  { char: "☆", name: "빈별", kw: "star empty" },
  { char: "♥", name: "하트", kw: "heart" },
  { char: "♦", name: "다이아카드", kw: "diamond card" },
  { char: "♣", name: "클로버", kw: "club" },
  { char: "♠", name: "스페이드", kw: "spade" },
  { char: "☑", name: "체크박스", kw: "check box" },
  { char: "☒", name: "엑스박스", kw: "cross box" },
  { char: "✓", name: "체크표시", kw: "tick" },
  { char: "✗", name: "엑스표시", kw: "ballot x" },
  { char: "§", name: "섹션", kw: "section" },
  { char: "¶", name: "단락", kw: "pilcrow paragraph" },
  { char: "©", name: "저작권", kw: "copyright" },
  { char: "®", name: "등록상표", kw: "registered" },
  { char: "™", name: "상표", kw: "trademark" },
  { char: "°", name: "도", kw: "degree" },
  { char: "′", name: "분", kw: "prime minute" },
  { char: "″", name: "초", kw: "double prime second" },
  { char: "…", name: "말줄임표", kw: "ellipsis" },
  { char: "–", name: "엔대시", kw: "en dash" },
  { char: "—", name: "엠대시", kw: "em dash" },
  { char: "‐", name: "하이픈", kw: "hyphen" },
  { char: "“", name: "여는큰따옴표", kw: "open double quote" },
  { char: "”", name: "닫는큰따옴표", kw: "close double quote" },
  { char: "‘", name: "여는작은따옴표", kw: "open single quote" },
  { char: "’", name: "닫는작은따옴표", kw: "close single quote" },
  { char: "«", name: "여는화살괄호", kw: "left guillemet" },
  { char: "»", name: "닫는화살괄호", kw: "right guillemet" },
  { char: "‹", name: "여는작은화살괄호", kw: "left single guillemet" },
  { char: "›", name: "닫는작은화살괄호", kw: "right single guillemet" },
];

// 화살표.
const ARROW: EmojiItem[] = [
  { char: "→", name: "오른쪽", kw: "right arrow" },
  { char: "←", name: "왼쪽", kw: "left arrow" },
  { char: "↑", name: "위", kw: "up arrow" },
  { char: "↓", name: "아래", kw: "down arrow" },
  { char: "⇒", name: "강조오른쪽", kw: "double right arrow implies" },
  { char: "⇐", name: "강조왼쪽", kw: "double left arrow" },
  { char: "⇑", name: "강조위", kw: "double up arrow" },
  { char: "⇓", name: "강조아래", kw: "double down arrow" },
  { char: "↔", name: "양쪽", kw: "left right arrow" },
  { char: "↕", name: "위아래", kw: "up down arrow" },
  { char: "⇄", name: "왼쪽두선", kw: "swap arrows" },
  { char: "⇆", name: "오른쪽두선", kw: "swap arrows" },
  { char: "⇋", name: "양방향위", kw: "rev equilibrium" },
  { char: "⇌", name: "양방향아래", kw: "equilibrium" },
  { char: "↩", name: "되돌리기", kw: "leftwards arrow with hook" },
  { char: "↪", name: "건너뛰기", kw: "rightwards arrow with hook" },
  { char: "↻", name: "시계방향", kw: "clockwise" },
  { char: "↺", name: "반시계방향", kw: "counter clockwise" },
  { char: "⤴", name: "오른쪽위", kw: "arrow up right" },
  { char: "⤵", name: "오른쪽아래", kw: "arrow down right" },
  { char: "▶", name: "재생", kw: "play right pointer" },
  { char: "◀", name: "왼쪽포인터", kw: "left pointer" },
  { char: "⏵", name: "재생2", kw: "play2" },
  { char: "⏴", name: "되감기2", kw: "rewind2" },
  { char: "➜", name: "굵은오른쪽", kw: "heavy arrow right" },
  { char: "➤", name: "포인터오른쪽", kw: "pointer right" },
];

// 수학·통화.
const MATH: EmojiItem[] = [
  { char: "±", name: "플러스마이너스", kw: "plus minus" },
  { char: "×", name: "곱", kw: "multiply times" },
  { char: "÷", name: "나눔", kw: "divide" },
  { char: "≠", name: "같지않음", kw: "not equal" },
  { char: "≈", name: "거의같음", kw: "approximately" },
  { char: "≤", name: "작거나같음", kw: "less or equal" },
  { char: "≥", name: "크거나같음", kw: "greater or equal" },
  { char: "∞", name: "무한", kw: "infinity" },
  { char: "∑", name: "합", kw: "sigma sum" },
  { char: "∏", name: "곱연산", kw: "pi product" },
  { char: "√", name: "루트", kw: "sqrt root" },
  { char: "∂", name: "편미분", kw: "partial" },
  { char: "∫", name: "적분", kw: "integral" },
  { char: "∈", name: "속함", kw: "element of" },
  { char: "∉", name: "속하지않음", kw: "not element of" },
  { char: "∋", name: "포함", kw: "contains" },
  { char: "⊂", name: "부분집합", kw: "subset" },
  { char: "⊃", name: "상위집합", kw: "superset" },
  { char: "∪", name: "합집합", kw: "union" },
  { char: "∩", name: "교집합", kw: "intersection" },
  { char: "∅", name: "공집합", kw: "empty set" },
  { char: "μ", name: "마이크로", kw: "mu micro" },
  { char: "Ω", name: "옴", kw: "ohm omega" },
  { char: "π", name: "파이", kw: "pi" },
  { char: "α", name: "알파", kw: "alpha" },
  { char: "β", name: "베타", kw: "beta" },
  { char: "γ", name: "감마", kw: "gamma" },
  { char: "Δ", name: "델타", kw: "delta change" },
  { char: "θ", name: "쎄타", kw: "theta" },
  { char: "λ", name: "람다", kw: "lambda" },
  { char: "σ", name: "시그마", kw: "sigma" },
  { char: "₩", name: "원화", kw: "won krw" },
  { char: "$", name: "달러", kw: "dollar usd" },
  { char: "€", name: "유로", kw: "euro" },
  { char: "¥", name: "엔/위안", kw: "yen yuan jpy cny" },
  { char: "£", name: "파운드", kw: "pound gbp" },
  { char: "¢", name: "센트", kw: "cent" },
];

export const EMOJI_CATEGORIES: EmojiCategory[] = [
  { key: "face", label: "표정", items: FACE },
  { key: "sym", label: "기호", items: SYM },
  { key: "arrow", label: "화살표", items: ARROW },
  { key: "math", label: "수학·통화", items: MATH },
];

// ---------------------------------------------------------------------------
// 검색 + 최근 사용 (localStorage).
// ---------------------------------------------------------------------------

const RECENT_KEY = "tiptap.emoji.recent";
const RECENT_MAX = 24;

export function getRecentEmojis(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x: unknown) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function pushRecentEmoji(char: string): string[] {
  if (typeof window === "undefined") return [];
  const cur = getRecentEmojis().filter((c) => c !== char);
  const next = [char, ...cur].slice(0, RECENT_MAX);
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // ignore quota / private mode 등.
  }
  return next;
}

// 모든 카테고리에서 char 로 EmojiItem 찾기 — 최근 탭 렌더링용.
export function findEmojiByChar(char: string): EmojiItem | undefined {
  for (const cat of EMOJI_CATEGORIES) {
    const it = cat.items.find((i) => i.char === char);
    if (it) return it;
  }
  return undefined;
}

// 검색 — 카테고리 무관 전체에서 name + kw + char 매칭. lowercase 비교.
export function searchEmojis(query: string): EmojiItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const out: EmojiItem[] = [];
  for (const cat of EMOJI_CATEGORIES) {
    for (const it of cat.items) {
      const hay =
        it.name.toLowerCase() + " " + (it.kw ?? "").toLowerCase() + " " + it.char;
      if (hay.includes(q)) out.push(it);
    }
  }
  return out;
}
