/**
 * lowlight 인스턴스 + 댓글 코드블록에서 사용할 언어 셋.
 *
 * lowlight 3.x 의 ``common`` 익스포트는 highlight.js 의 자주 쓰이는 언어
 * (~37개: bash, c, c++, c#, css, diff, go, graphql, ini, java, javascript,
 *  json, kotlin, less, lua, makefile, markdown, objectivec, perl, php,
 *  php-template, plaintext, python, python-repl, r, ruby, rust, scss,
 *  shell, sql, swift, typescript, vbnet, wasm, xml, yaml) 를 묶어둔 셋.
 *
 * 개별 ``import xxx from "highlight.js/lib/languages/xxx"`` + ``register``
 * 패턴은 Next.js 의 ESM 환경에서 default-export wrapping 으로 silently
 * 실패하는 경우가 있어 ``createLowlight(common)`` 통합 패턴을 사용한다.
 *
 * 셀렉트 UI 에 노출할 (key, label) 라벨은 별도로 관리한다. 등록 안 된 언어
 * 키는 highlight 없이 plain 으로 렌더되므로 안전 — UI 와 등록 셋이 일치하지
 * 않아도 동작이 깨지진 않는다.
 */

import { common, createLowlight } from "lowlight"

export const lowlight = createLowlight(common)

// 셀렉트 UI 에서 사용할 (key, label) 리스트. key 가 곧 ``<code class="language-<key>">``
// 의 suffix 이자 lowlight 의 등록 이름이다.
export const LANGUAGE_OPTIONS: Array<{ key: string; label: string }> = [
  { key: "plaintext", label: "Plain text" },
  { key: "bash", label: "Bash" },
  { key: "c", label: "C" },
  { key: "cpp", label: "C++" },
  { key: "csharp", label: "C#" },
  { key: "css", label: "CSS" },
  { key: "diff", label: "Diff" },
  { key: "go", label: "Go" },
  { key: "graphql", label: "GraphQL" },
  { key: "ini", label: "INI" },
  { key: "java", label: "Java" },
  { key: "javascript", label: "JavaScript" },
  { key: "json", label: "JSON" },
  { key: "kotlin", label: "Kotlin" },
  { key: "less", label: "Less" },
  { key: "lua", label: "Lua" },
  { key: "makefile", label: "Makefile" },
  { key: "markdown", label: "Markdown" },
  { key: "objectivec", label: "Objective-C" },
  { key: "perl", label: "Perl" },
  { key: "php", label: "PHP" },
  { key: "python", label: "Python" },
  { key: "r", label: "R" },
  { key: "ruby", label: "Ruby" },
  { key: "rust", label: "Rust" },
  { key: "scss", label: "SCSS" },
  { key: "shell", label: "Shell" },
  { key: "sql", label: "SQL" },
  { key: "swift", label: "Swift" },
  { key: "typescript", label: "TypeScript" },
  { key: "xml", label: "HTML / XML" },
  { key: "yaml", label: "YAML" },
]
