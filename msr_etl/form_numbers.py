import os
import re

import pandas as pd

DEFAULT_FORM_NUMBER_COLUMN = "form_number"
# Rejected rows kept for the report; the counts stay exact beyond this
MAX_REPORTED_REJECTIONS = 500

_READERS = {
    # skip_blank_lines=False keeps reported row numbers matching the file
    ".csv": lambda f: pd.read_csv(f, dtype=str, keep_default_na=False, skip_blank_lines=False),
    ".xlsx": lambda f: pd.read_excel(f, dtype=str, keep_default_na=False),
    ".xls": lambda f: pd.read_excel(f, dtype=str, keep_default_na=False),
}
# Excel stores numbers as floats, so 30874398 can come back as "30874398.0"
_EXCEL_FLOAT = re.compile(r"^(\d+)\.0+$")


class FormNumberFileError(ValueError):
    pass


def _normalise_header(header):
    return re.sub(r"[\s_-]+", "_", str(header).strip().lower())


def _pick_column(df, column):
    wanted = _normalise_header(column)
    for header in df.columns:
        if _normalise_header(header) == wanted:
            return header
    if len(df.columns) == 1:
        return df.columns[0]
    raise FormNumberFileError(
        f"No '{column}' column; the file has: {', '.join(str(h) for h in df.columns)}"
    )


def read_form_numbers(upload_file, column=DEFAULT_FORM_NUMBER_COLUMN):
    """Read the households to import from a file of form numbers (e.g. UPG's SCTP export).

    Returns {"form_numbers": [...], "rejected_rows": [...], "rejected_count", "duplicate_count",
    "row_count"}. Blank rows are ignored; repeated form numbers are kept once.
    """
    extension = os.path.splitext(getattr(upload_file, "name", "") or "")[1].lower()
    reader = _READERS.get(extension)
    if not reader:
        raise FormNumberFileError(f"Unsupported file type '{extension or 'none'}'; use .xlsx, .xls or .csv")
    try:
        df = reader(upload_file)
    except Exception as exc:
        raise FormNumberFileError(f"Could not read the file: {exc}") from exc

    header = _pick_column(df, column)
    form_numbers, seen, rejected = [], set(), []
    rejected_count = duplicate_count = 0
    for index, raw in enumerate(df[header].tolist()):
        value = str(raw).strip()
        if not value:
            continue
        value = _EXCEL_FLOAT.sub(r"\1", value)
        if not value.isdigit():
            rejected_count += 1
            if len(rejected) < MAX_REPORTED_REJECTIONS:
                # +2: a header line, and rows counted from 1
                rejected.append({"row": index + 2, "value": str(raw), "reason": "not a number"})
            continue
        if value in seen:
            duplicate_count += 1
            continue
        seen.add(value)
        form_numbers.append(value)

    if not form_numbers:
        raise FormNumberFileError("The file has no usable form numbers")
    return {
        "form_numbers": form_numbers,
        "rejected_rows": rejected,
        "rejected_count": rejected_count,
        "duplicate_count": duplicate_count,
        "row_count": len(df.index),
    }
