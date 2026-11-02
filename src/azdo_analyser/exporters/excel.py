"""Multi-sheet Excel workbook with tables (pivot-ready), filters, conditional formatting and hyperlinks."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from azdo_analyser.core.base import Exporter
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_exporter
from azdo_analyser.exporters.tables import SHEETS

SEVERITY_FILLS = {
    "CRITICAL": ("7B1FA2", "FFFFFF"),
    "HIGH": ("C62828", "FFFFFF"),
    "MEDIUM": ("EF6C00", "FFFFFF"),
    "LOW": ("F9A825", "000000"),
    "INFO": ("90A4AE", "000000"),
}
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
MAX_CELL = 32000
SCORE_COLUMNS = {"Complexity Score", "Days Since Commit", "Days Since Use", "Age (days)", "Lead Time (days)",
                 "Cycle Time (days)"}
RATE_COLUMNS = {"Success Rate", "Automation %"}
BOOL_RISK_COLUMNS = {"Unused", "Open Access", "Orphaned"}


def _clean(value):
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
        return value[:MAX_CELL]
    if isinstance(value, (list, tuple, set)):
        return _clean(", ".join(str(v) for v in value))
    if isinstance(value, dict):
        return _clean(str(value))
    return value


def _table_name(sheet: str) -> str:
    return "tbl" + re.sub(r"[^A-Za-z0-9]", "", sheet)


@register_exporter("excel", order=30)
class ExcelExporter(Exporter):
    """Excel/ProjectAssessment.xlsx."""

    folder = "Excel"

    def enabled(self, settings: Settings) -> bool:
        return settings.generate_excel

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:
        path = self.target(output_dir) / "ProjectAssessment.xlsx"
        frames = {}
        for name, builder, _ in SHEETS:
            rows = builder(ctx)
            df = pd.DataFrame([{k: _clean(v) for k, v in r.items()} for r in rows])
            frames[name] = df
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for name, df in frames.items():
                if df.empty:
                    pd.DataFrame({"Info": ["No data collected for this section (see Coverage sheet)."]}).to_excel(
                        writer, sheet_name=name, index=False)
                else:
                    df.to_excel(writer, sheet_name=name, index=False)
        self._format(path, frames, {n: d for n, _, d in SHEETS})
        return [path]

    def _format(self, path: Path, frames: dict[str, pd.DataFrame], descriptions: dict[str, str]) -> None:
        wb = load_workbook(path)
        for ws in wb.worksheets:
            df = frames.get(ws.title)
            max_row, max_col = ws.max_row, ws.max_column
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = HEADER_FILL
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.freeze_panes = "B2" if ws.title not in ("Summary", "Metrics") else "A2"
            if df is not None and not df.empty and max_row > 1:
                ref = f"A1:{get_column_letter(max_col)}{max_row}"
                table = Table(displayName=_table_name(ws.title), ref=ref)
                table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
                ws.add_table(table)  # tables provide filters + structured, pivot-ready ranges
            headers = [c.value for c in ws[1]]
            for idx, header in enumerate(headers, start=1):
                letter = get_column_letter(idx)
                values = [len(str(c.value)) for c in ws[letter][: min(max_row, 300)] if c.value is not None]
                ws.column_dimensions[letter].width = max(10, min(60, (max(values) if values else 10) + 2))
                rng = f"{letter}2:{letter}{max(max_row, 2)}"
                if header == "Severity":
                    for sev, (bg, fg) in SEVERITY_FILLS.items():
                        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{sev}"'],
                                                                      fill=PatternFill("solid", fgColor=bg),
                                                                      font=Font(color=fg, bold=True)))
                elif header in SCORE_COLUMNS:
                    ws.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="E8F5E9",
                                                                      mid_type="percentile", mid_value=50,
                                                                      mid_color="FFF59D", end_type="max",
                                                                      end_color="EF9A9A"))
                elif header in RATE_COLUMNS:
                    ws.conditional_formatting.add(rng, DataBarRule(start_type="num", start_value=0, end_type="num",
                                                                   end_value=1, color="43A047"))
                    for c in ws[letter][1:]:
                        c.number_format = "0%"
                elif header in BOOL_RISK_COLUMNS:
                    ws.conditional_formatting.add(rng, FormulaRule(formula=[f"{letter}2=TRUE"],
                                                                   fill=PatternFill("solid", fgColor="FFCDD2")))
                elif header in ("Remediation Steps", "Description", "Impact", "Recommendation", "Message"):
                    ws.column_dimensions[letter].width = 60
                    for c in ws[letter][1:]:
                        c.alignment = Alignment(wrap_text=True, vertical="top")
                if header == "URL":
                    for c in ws[letter][1:]:
                        if isinstance(c.value, str) and c.value.startswith("http"):
                            c.hyperlink = c.value
                            c.value = "Open"
                            c.style = "Hyperlink"
                for c in ws[letter][1:]:
                    if hasattr(c.value, "year"):
                        c.number_format = "yyyy-mm-dd hh:mm"
            if ws.title == "Summary":
                ws.column_dimensions["A"].width = 42
                ws.column_dimensions["B"].width = 80
        index = wb.create_sheet("Index", 0)
        index.append(["Sheet", "Contents"])
        for cell in index[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = HEADER_FILL
        for name, desc in descriptions.items():
            index.append([name, desc])
            cell = index.cell(row=index.max_row, column=1)
            cell.hyperlink = f"#'{name}'!A1"
            cell.style = "Hyperlink"
        index.column_dimensions["A"].width = 26
        index.column_dimensions["B"].width = 70
        wb.active = 0
        wb.save(path)
