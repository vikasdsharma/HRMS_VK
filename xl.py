"""Excel output safety. openpyxl treats any text starting with '=' as a live formula, so a worker named
'=HYPERLINK(...)' would become an active formula in every export (formula injection). Every workbook we
produce goes through to_buffer(), which turns formula cells back into plain text."""
import io
import zipfile

import config


def neutralize_formulas(wb):
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.data_type == "f":
                    c.data_type = "s"
    return wb


def to_buffer(wb):
    neutralize_formulas(wb)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class UploadRejected(ValueError):
    pass


def check_xlsx_bytes(data, max_mb=None):
    """Reject files that are not a sane .xlsx before openpyxl ever touches them (type, size, zip bombs)."""
    max_bytes = (max_mb or config.MAX_UPLOAD_MB) * 1024 * 1024
    if len(data) > max_bytes:
        raise UploadRejected(f"File is larger than {max_mb or config.MAX_UPLOAD_MB} MB")
    if not data.startswith(b"PK"):
        raise UploadRejected("This is not an Excel (.xlsx) file")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > 500:
                raise UploadRejected("Excel file has too many internal parts")
            total = sum(i.file_size for i in infos)
            if total > 200 * 1024 * 1024 or (len(data) and total / len(data) > 200):
                raise UploadRejected("Excel file expands to an unreasonable size")
    except zipfile.BadZipFile:
        raise UploadRejected("This is not a valid Excel (.xlsx) file")
