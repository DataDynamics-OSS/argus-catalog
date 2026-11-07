// DDL 탭 카드 제목 — 원천 DDL 종류(테이블/뷰/구체화 뷰)에 맞춰 표시한다.
export function ddlTitle(ddl: string | null | undefined): string {
  const head = (ddl ?? "")
    .replace(/^\s*(--[^\n]*\n\s*)*/, "")
    .slice(0, 200)
    .toUpperCase()
  if (/^CREATE\s+(OR\s+REPLACE\s+)?(\S+\s+)*?MATERIALIZED\s+VIEW\b/.test(head)) return "CREATE MATERIALIZED VIEW DDL"
  if (/^CREATE\s+(OR\s+REPLACE\s+)?(\S+\s+)*?VIEW\b/.test(head)) return "CREATE VIEW DDL"
  return "CREATE TABLE DDL"
}
