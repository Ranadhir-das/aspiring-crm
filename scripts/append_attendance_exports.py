import os

with open('apps/web/export_utils.py', 'r', encoding='utf-8') as f:
    content = f.read()

snippet = '''

# -----------------------------------------------------------------------------
# Attendance Report Export Definitions
# -----------------------------------------------------------------------------

ATTENDANCE_EXPORT_COLUMNS = [
    ("Employee ID", "employee_id"),
    ("Employee Name", "employee_name"),
    ("Username", "username"),
    ("Role / Department", "role"),
    ("Primary Branch", "branch"),
    ("Date", "date"),
    ("Shift Start", "shift_start"),
    ("Shift End", "shift_end"),
    ("Gross Duration", "gross_duration"),
    ("Break Duration", "break_duration"),
    ("Net Working Hours", "net_hours"),
    ("Required Hours", "required_hours"),
    ("Attendance Status", "status"),
    ("Exception / Correction", "exception"),
    ("Notes", "notes"),
]


def attendance_record_to_row(rec):
    return [
        rec.employee_id,
        sanitize_cell(rec.employee_name),
        sanitize_cell(rec.username),
        sanitize_cell(rec.role_display),
        sanitize_cell(f"{rec.primary_branch_name} ({rec.primary_branch_code})"),
        rec.date.strftime("%Y-%m-%d"),
        rec.first_start_formatted,
        rec.final_end_formatted,
        rec.gross_formatted,
        rec.break_formatted,
        rec.net_work_hours,
        rec.required_work_hours,
        sanitize_cell(rec.status_label),
        sanitize_cell(rec.exception_status or "-"),
        sanitize_cell(rec.notes or "-"),
    ]


def export_attendance_csv(records, filename="attendance_report.csv"):
    """Exports attendance records to a downloadable CSV stream with formula sanitization."""
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [col[0] for col in ATTENDANCE_EXPORT_COLUMNS]
    writer.writerow(headers)

    for rec in records:
        writer.writerow(attendance_record_to_row(rec))

    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_attendance_excel(records, filename="attendance_report.xlsx"):
    """Exports attendance records to formatted Excel (.xlsx) file."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance Records"

    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="3B2D60", end_color="3B2D60", fill_type="solid")
    regular_font = Font(name="Segoe UI", size=10)
    summary_font = Font(name="Segoe UI", size=10, bold=True)
    summary_fill = PatternFill(start_color="F1EEF9", end_color="F1EEF9", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin", color="DCE1EC"),
        right=Side(style="thin", color="DCE1EC"),
        top=Side(style="thin", color="DCE1EC"),
        bottom=Side(style="thin", color="DCE1EC"),
    )

    headers = [col[0] for col in ATTENDANCE_EXPORT_COLUMNS]
    ws.append(headers)
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
        cell.border = thin_border
    ws.row_dimensions[1].height = 28

    row_idx = 2
    total_net_hours = 0.0
    for rec in records:
        row_data = attendance_record_to_row(rec)
        ws.append(row_data)
        ws.row_dimensions[row_idx].height = 20
        total_net_hours += rec.net_work_hours
        for col_num in range(1, len(row_data) + 1):
            cell = ws.cell(row=row_idx, column=col_num)
            cell.font = regular_font
            cell.border = thin_border
            if col_num in (1, 11, 12):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_num in (6, 7, 8, 9, 10):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
        row_idx += 1

    # Add Summary Row at the bottom
    summary_row = [
        "Total",
        f"{len(records)} records",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Net Total:",
        round(total_net_hours, 2),
        "",
        "",
        "",
        "",
    ]
    ws.append(summary_row)
    ws.row_dimensions[row_idx].height = 24
    for col_num in range(1, len(summary_row) + 1):
        cell = ws.cell(row=row_idx, column=col_num)
        cell.font = summary_font
        cell.fill = summary_fill
        cell.border = thin_border
        if col_num in (1, 10, 11):
            cell.alignment = Alignment(horizontal="right", vertical="center")

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 40)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    response = HttpResponse(
        bio.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
'''

with open('apps/web/export_utils.py', 'w', encoding='utf-8', newline='') as f:
    f.write(content + snippet)

print("Successfully appended attendance export functions to apps/web/export_utils.py")

