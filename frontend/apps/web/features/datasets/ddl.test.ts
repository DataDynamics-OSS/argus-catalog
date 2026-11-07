import { describe, expect, it } from "vitest"
import { ddlTitle } from "./ddl"

describe("ddlTitle", () => {
  it("뷰 정의는 CREATE VIEW", () => {
    expect(ddlTitle('CREATE OR REPLACE VIEW "shop"."v" AS\nSELECT 1;')).toBe("CREATE VIEW DDL")
    // MySQL SHOW CREATE VIEW 형식
    expect(ddlTitle("CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`%` SQL SECURITY DEFINER VIEW `v` AS select 1")).toBe("CREATE VIEW DDL")
    expect(ddlTitle("CREATE MATERIALIZED VIEW s.mv AS SELECT 1")).toBe("CREATE MATERIALIZED VIEW DDL")
  })
  it("테이블·빈 값은 CREATE TABLE", () => {
    expect(ddlTitle('CREATE TABLE "shop"."customers" (\n  "id" int4\n);')).toBe("CREATE TABLE DDL")
    expect(ddlTitle("CREATE TABLE review_view (id int)")).toBe("CREATE TABLE DDL")
    expect(ddlTitle(null)).toBe("CREATE TABLE DDL")
  })
})
