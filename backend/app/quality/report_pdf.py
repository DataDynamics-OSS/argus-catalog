# SPDX-License-Identifier: Apache-2.0
"""작업 실행 리포트의 PDF — 엑셀(``reports.report_xlsx``)과 같은 내용을 인쇄·공유용 한 문서로.

구성: 제목 · 핵심 숫자 · 실행 정보 · 영역별 · 분류별 · 데이터셋별(오류율 높은 순) · 시작하지 못한 데이터셋 · 규칙별(실패 먼저).
A4 가로. 한글은 함께 넣은 Noto Sans KR(``fonts/``, SIL OFL 1.1)로 찍는다 — 서버에 한글 글꼴이 없어도 된다.
이 글꼴에 없는 글자(한자·이모지 등)는 빈 네모로 나온다.

규칙별 표는 ``RULE_ROWS`` 줄까지만 싣는다 — 전체는 엑셀로 받는다.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from app.quality import indicators as quality_indicators
from app.quality.reports import _STATUS, _local, _zone, check_type_label, display_zone, severity_label
from app.quality.schemas import QualityJobRunReport

# 규칙별 표에 싣는 최대 줄 수 — 넘으면 실패한 규칙부터 싣고 나머지는 엑셀로 안내한다
RULE_ROWS = 1000

_FONT_DIR = Path(__file__).parent / "fonts"
_FONT = "NotoSansKR"
_FONT_BOLD = "NotoSansKR-Bold"

_INK = "#111827"
_MUTED = "#6B7280"
_LINE = "#D1D5DB"
_HEAD = "#E5E7EB"
_STRIPE = "#F9FAFB"
_BAD = "#B91C1C"


def _register_fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(_FONT, str(_FONT_DIR / "NotoSansKR-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(_FONT_DIR / "NotoSansKR-Bold.ttf")))
    pdfmetrics.registerFontFamily(_FONT, normal=_FONT, bold=_FONT_BOLD, italic=_FONT, boldItalic=_FONT_BOLD)


# 함께 넣은 Noto Sans KR 에 없는 기호 — 그대로 두면 빈 네모로 찍히고 텍스트 추출에서 빠져 "기대 ≥ 0" 이 "기대 0" 으로
# 뜻이 바뀐다(DEF-134). 진단 결과 문구 · 규칙 기준에 나오는 비교 · 화살표 기호를 ASCII 로 바꿔 찍는다.
_NO_GLYPH_MAP = {"≥": ">=", "≤": "<=", "≠": "!=", "→": "->", "←": "<-", "⇒": "=>", "≈": "~", "✓": "v", "✗": "x", "‰": "permil",
                 "\ufffd": "?",  # 대체 문자(깨진 글자 진단의 설명 · 위반 값)
                 "\u3000": " "}  # 전각 공백(공백 진단의 패턴 · 위반 값)
_NO_GLYPH_CHARS = tuple(_NO_GLYPH_MAP)
_NO_GLYPH = str.maketrans(_NO_GLYPH_MAP)


def _pdf_text(text: object) -> str:
    """PDF 에 찍을 문자열 — 글꼴에 없는 기호를 ASCII 로 바꾼다."""
    return ("" if text is None else str(text)).translate(_NO_GLYPH)


def _num(value: int | None) -> str:
    return f"{value:,}" if value is not None else "-"


def _rate(rate: float | None) -> str:
    return f"{rate:.2f}%" if rate is not None else "-"


def report_pdf(report: QualityJobRunReport, tz: str | None = None) -> bytes:
    """리포트를 PDF 파일로. ``tz`` 는 시각을 적을 IANA 시간대(화면의 브라우저 시간대, ``reports.display_zone``)."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_fonts()
    ink, muted, bad = colors.HexColor(_INK), colors.HexColor(_MUTED), colors.HexColor(_BAD)
    base = ParagraphStyle("base", fontName=_FONT, fontSize=8, leading=11, textColor=ink, wordWrap="CJK")
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=base, fontName=_FONT_BOLD)
    head_right = ParagraphStyle("head_right", parent=head, alignment=TA_RIGHT)
    title = ParagraphStyle("title", parent=base, fontName=_FONT_BOLD, fontSize=16, leading=22)
    subtitle = ParagraphStyle("subtitle", parent=base, fontSize=9, leading=13, textColor=muted)
    section = ParagraphStyle("section", parent=base, fontName=_FONT_BOLD, fontSize=10.5, leading=15, spaceBefore=10,
                             spaceAfter=4)
    note = ParagraphStyle("note", parent=base, textColor=muted)
    tile_label = ParagraphStyle("tile_label", parent=base, textColor=muted)
    tile_value = ParagraphStyle("tile_value", parent=base, fontName=_FONT_BOLD, fontSize=15, leading=20)
    tile_value_bad = ParagraphStyle("tile_value_bad", parent=tile_value, textColor=bad)

    page = landscape(A4)
    margin = 14 * mm
    width = page[0] - 2 * margin

    def p(text, style=base) -> Paragraph:
        return Paragraph(escape(_pdf_text(text)), style)

    def table(headers: list[str], rows: list[list], widths: list[float], numeric: set[int] = frozenset(),
              bad_rows: set[int] = frozenset()) -> Table:
        """머리글이 쪽마다 되풀이되는 표. ``widths`` 는 비율, ``numeric`` 은 오른쪽으로 붙이는 열."""
        total = sum(widths)
        data = [[p(h, head_right if c in numeric else head) for c, h in enumerate(headers)]]
        for row in rows:
            data.append([p(v, right if c in numeric else base) for c, v in enumerate(row)])
        t = Table(data, colWidths=[width * w / total for w in widths], repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEAD)),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(_LINE)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_STRIPE)]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        # 실패한 줄은 색만으로 알리지 않는다 — "결과"·"실패한 규칙" 열에 글자가 있고, 왼쪽에 굵은 선을 더한다
        style += [("LINEBEFORE", (0, r + 1), (0, r + 1), 2, bad) for r in sorted(bad_rows)]
        t.setStyle(TableStyle(style))
        return t

    jr = report.job_run
    tzi = display_zone(tz)
    zone = _zone(tzi)
    names = {i.key: i.name for i in quality_indicators.INDICATORS}
    job_name = jr.job_name or f"작업 #{jr.job_id}"
    story: list = [
        p("데이터 품질 진단 리포트", title),
        p(f"{job_name} · 작업 실행 #{jr.id} · {_local(jr.started_at, tzi) or '-'} ({zone})", subtitle),
        Spacer(1, 8),
    ]

    # 핵심 숫자
    score = jr.score  # 실행 현황·작업 이력과 같은 값 — 평가한 규칙의 통과 비율(데이터셋 실행이 여럿이면 평균)
    tiles = [
        ("품질 점수", f"{score:.2f}" if score is not None else "-", False),
        ("오류율", _rate(jr.error_rate), bool(jr.error_rate)),
        ("진단 건수", _num(jr.checked_count), False),
        ("오류 건수", _num(jr.error_count), bool(jr.error_count)),
        ("실패한 규칙", f"{report.failed_rules:,} / {report.rules_total:,}", report.failed_rules > 0),
        ("진단한 데이터셋", f"{jr.runs_succeeded:,} / {jr.runs_total:,}", jr.runs_failed > 0),
    ]
    tile_table = Table(
        [[p(label, tile_label) for label, _, _ in tiles],
         [p(value, tile_value_bad if alert else tile_value) for _, value, alert in tiles]],
        colWidths=[width / len(tiles)] * len(tiles))
    tile_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor(_LINE)),
        ("LINEAFTER", (0, 0), (-2, -1), 0.4, colors.HexColor(_LINE)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
    ]))
    story += [tile_table, p("품질 점수 = 이 실행에서 평가한 규칙 가운데 통과한 비율(100점 만점). 오류율 = 오류 건수 합 ÷ 진단 건수 합. "
                            "진단한 데이터셋은 성공 / 전체입니다.", note)]

    # 실행 정보 — 항목과 값을 두 쌍씩
    facts = [
        ("품질 진단 작업", job_name),
        ("상태", _STATUS.get(jr.status, jr.status)),
        (f"시작 ({zone})", _local(jr.started_at, tzi) or "-"),
        (f"종료 ({zone})", _local(jr.finished_at, tzi) or "-"),
        ("시작 방식", "예약" if jr.trigger_type == "SCHEDULE" else f"수동 ({jr.requested_by or '-'})"),
        ("대상 데이터셋", _num(jr.targets_total)),
        ("품질 지표", ", ".join(jr.definitions) or "전체"),
        ("진단 기준", ", ".join(names.get(k, k) for k in jr.indicators) or "전체"),
        ("실패·일부 실패 / 중지", f"{_num(jr.runs_failed)} / {_num(jr.runs_cancelled)}"),
        ("시작하지 못한 데이터셋", _num(jr.skipped_total)),
        ("규칙이 없어 진단하지 않은 데이터셋", _num(jr.no_rules)),
        ("비고", jr.message or "-"),
    ]
    fact_rows = []
    for i in range(0, len(facts), 2):
        (k1, v1), (k2, v2) = facts[i], facts[i + 1]
        fact_rows.append([p(k1, head), p(v1), p(k2, head), p(v2)])
    fact_table = Table(fact_rows, colWidths=[width * 0.22, width * 0.28, width * 0.22, width * 0.28])
    fact_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(_LINE)),
        ("LINEABOVE", (0, 0), (-1, 0), 0.4, colors.HexColor(_LINE)),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(_STRIPE)),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor(_STRIPE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [p("실행 정보", section), fact_table]

    if report.areas:
        story += [p("영역별", section), table(
            ["영역", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율"],
            [[g.name, _num(g.rules), _num(g.failed_rules), _num(g.checked_count), _num(g.error_count),
              _rate(g.error_rate)] for g in report.areas],
            [30, 12, 14, 16, 16, 12], numeric={1, 2, 3, 4, 5},
            bad_rows={i for i, g in enumerate(report.areas) if g.failed_rules})]
        story += [p("진단 기준별", section), table(
            ["영역", "진단 기준", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율"],
            [[g.area_name, g.name, _num(g.rules), _num(g.failed_rules), _num(g.checked_count), _num(g.error_count),
              _rate(g.error_rate)] for g in report.indicators],
            [14, 24, 10, 12, 14, 14, 12], numeric={2, 3, 4, 5, 6},
            bad_rows={i for i, g in enumerate(report.indicators) if g.failed_rules})]
        if report.ai_ready_indicators:
            # 같은 결과를 AI-Ready 품질 지표(가이드라인 §2.3)로 — 지표 합계 행 뒤에 진단 기준 행
            rows = [[a.name, "(합계)", "", _num(a.rules), _num(a.failed_rules), _num(a.checked_count),
                     _num(a.error_count), _rate(a.error_rate)] for a in report.ai_ready_areas]
            rows += [[g.area_name, g.ai_ready_detail or "-", g.name, _num(g.rules), _num(g.failed_rules),
                      _num(g.checked_count), _num(g.error_count), _rate(g.error_rate)] for g in report.ai_ready_indicators]
            failed = [a.failed_rules for a in report.ai_ready_areas] + [g.failed_rules for g in report.ai_ready_indicators]
            story += [p("AI-Ready 품질 지표별", section), table(
                ["AI-Ready 지표", "세부 지표", "진단 기준", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율"],
                rows, [12, 16, 18, 8, 10, 12, 12, 12], numeric={3, 4, 5, 6, 7},
                bad_rows={i for i, f in enumerate(failed) if f})]
    else:
        story += [p("영역별 · 진단 기준별", section), p("이 실행에서 평가한 규칙이 없습니다.", note)]

    story += [p(f"데이터셋별 — 오류율 높은 순 ({len(report.datasets):,})", section)]
    if report.datasets:
        story.append(table(
            ["데이터셋", "원본", "진단 테이블", "상태", "규칙", "실패한 규칙", "진단 건수", "오류 건수", "오류율", "오류 내용"],
            [[d.dataset_name or f"데이터셋 #{d.dataset_id}", d.datasource_name or "-", d.engine_table or "-",
              _STATUS.get(d.status, d.status), _num(d.rules), _num(d.failed_rules), _num(d.checked_count),
              _num(d.error_count), _rate(d.error_rate), d.error_message or ""] for d in report.datasets],
            [22, 14, 26, 9, 7, 10, 12, 11, 9, 24], numeric={4, 5, 6, 7, 8},
            bad_rows={i for i, d in enumerate(report.datasets) if d.failed_rules or d.status != "SUCCEEDED"}))
    else:
        story.append(p("진단한 데이터셋이 없습니다.", note))

    if jr.skipped:
        story += [p(f"시작하지 못한 데이터셋 ({len(jr.skipped):,})", section), table(
            ["데이터셋", "사유"],
            [[s.name or f"데이터셋 #{s.dataset_id}", s.reason] for s in jr.skipped], [30, 70])]

    # 규칙별 — 실패한 규칙을 먼저(같은 결과끼리는 리포트의 순서 그대로)
    rules = sorted(report.rules, key=lambda r: r.passed)
    shown = rules[:RULE_ROWS]
    story += [p(f"규칙별 — 실패한 규칙 먼저 ({len(rules):,})", section)]
    if report.rules_missing:
        story.append(p(f"평가한 뒤 지워진 규칙 {report.rules_missing:,}개의 결과는 남아 있지 않아 표에 없습니다 — 위 요약의 건수와 다를 수 있습니다.", note))
    if len(rules) > RULE_ROWS:
        story.append(p(f"규칙이 많아 {RULE_ROWS:,}건만 실었습니다. 전체는 엑셀로 내려받으세요.", note))
    if shown:
        story.append(table(
            ["데이터셋", "규칙", "유형", "컬럼", "진단 기준", "품질 지표", "심각도", "결과", "진단 건수", "오류 건수", "오류율", "상세"],
            [[r.dataset_name or f"데이터셋 #{r.dataset_id}", r.rule_name or "-", check_type_label(r.check_type) or "-",
              r.column_name or "-", r.indicator_name or "-", r.definition_name or "-", severity_label(r.severity) or "-",
              "통과" if r.passed else "실패", _num(r.checked_count), _num(r.error_count), _rate(r.error_rate),
              r.detail or ""] for r in shown],
            [16, 20, 13, 13, 10, 14, 11, 6, 10, 9, 8, 23], numeric={8, 9, 10},
            bad_rows={i for i, r in enumerate(shown) if not r.passed}))
    else:
        story.append(p("이 실행에서 평가한 규칙이 없습니다.", note))

    made = datetime.now(tzi).strftime("%Y-%m-%d %H:%M:%S")

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(_FONT, 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(margin, 8 * mm, _pdf_text(f"Argus Catalog · {job_name} · 작업 실행 #{jr.id} · 만든 시각 {made} ({zone})"))
        canvas.drawRightString(page[0] - margin, 8 * mm, f"{doc.page} 쪽")
        canvas.restoreState()

    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=page, leftMargin=margin, rightMargin=margin, topMargin=12 * mm, bottomMargin=14 * mm,
        title=f"데이터 품질 진단 리포트 — {job_name} #{jr.id}", author="Argus Catalog", subject="데이터 품질 진단")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
