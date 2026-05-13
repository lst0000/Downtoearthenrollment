"""
Enrollsy Form Converter
-----------------------
A small Streamlit app that converts raw Enrollsy CSV exports into clean import-ready CSVs.

Supported output types:
1. weekly            -> adds Day, Group, Semester
2. summer_camp       -> adds Group, week_number
3. outdoor_discovery -> adds Group, When

Duplicate removal:
Upload an optional existing names/file CSV and the app will remove students already present.
Duplicate matching uses normalized first name + last name + birthdate when possible, and falls
back to first name + last name when birthdate is missing.

Run:
    pip install streamlit
    streamlit run enrollsy_form_converter_app.py
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import streamlit as st
except ImportError:  # Allows CLI use/import without Streamlit installed.
    st = None


# -----------------------------
# Config
# -----------------------------

COMMON_OUTPUT_COLUMNS = [
    "className",
    "first_name",
    "last_name",
    "middle_name",
    "birthdate",
    "primary_first_name",
    "primary_last_name",
    "primary_phone",
    "secondary_first_name",
    "secondary_last_name",
    "secondary_phone",
    "allergies",
    "photo_consent",
]

OUTPUT_COLUMNS_BY_TYPE = {
    "weekly": COMMON_OUTPUT_COLUMNS + ["Day", "Group", "Semester"],
    "summer_camp": COMMON_OUTPUT_COLUMNS + ["Group", "week_number"],
    "outdoor_discovery": COMMON_OUTPUT_COLUMNS + ["Group", "When"],
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Customize this if your summer week dates change.
DEFAULT_WEEK_KEY = {
    "june 8": "Week 1",
    "june 15": "Week 2",
    "june 22": "Week 3",
    "june 29": "Week 4",
    "july 6": "Week 5",
    "july 13": "Week 6",
    "july 20": "Week 7",
    "july 27": "Week 8",
    "august 3": "Week 9",
}

# Enrollsy's custom question headers are long and sometimes duplicated. These fragments make
# the converter resilient when the exact wording changes slightly.
FIELD_FRAGMENTS = {
    "allergies": ["allerg"],
    "photo_consent": ["photo/video permission", "photographs/video", "photo"],
    "day_of_week": ["day of the week", "day of the weekend"],
    "age_group": ["age group"],
}


# -----------------------------
# CSV helpers
# -----------------------------

def read_csv_upload(uploaded_file) -> List[Dict[str, str]]:
    """Read Streamlit upload or file-like object into a list of dictionaries."""
    content = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(content)))


def rows_to_csv_bytes(rows: List[Dict[str, str]], fieldnames: List[str]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


# -----------------------------
# Normalization helpers
# -----------------------------

def clean_text(value: Optional[str]) -> str:
    return " ".join((value or "").strip().split())


def normalize_name(value: Optional[str]) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]", "", value)
    return value


def normalize_phone(value: Optional[str]) -> str:
    digits = re.sub(r"\D", "", value or "")
    # Enrollsy often exports US phones with leading country code 1.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_date(value: Optional[str]) -> str:
    """Return YYYY-MM-DD when date can be parsed, otherwise return cleaned original."""
    value = clean_text(value)
    if not value:
        return ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def duplicate_key(row: Dict[str, str]) -> Tuple[str, str, str]:
    """Build a stable duplicate key from either Enrollsy or formatted output columns."""
    first = row.get("first_name") or row.get("enrolleeFirstName") or ""
    last = row.get("last_name") or row.get("enrolleeLastName") or ""
    birthdate = row.get("birthdate") or row.get("enrolleeBirthdate") or ""
    return (normalize_name(first), normalize_name(last), normalize_date(birthdate))


def fallback_name_key(row: Dict[str, str]) -> Tuple[str, str]:
    first = row.get("first_name") or row.get("enrolleeFirstName") or ""
    last = row.get("last_name") or row.get("enrolleeLastName") or ""
    return (normalize_name(first), normalize_name(last))


# -----------------------------
# Field extraction helpers
# -----------------------------

def first_matching_column(row: Dict[str, str], fragments: Iterable[str]) -> str:
    """Find first non-empty value where the column header contains one of the fragments."""
    lowered_fragments = [fragment.lower() for fragment in fragments]
    for key, value in row.items():
        key_lower = key.lower()
        if any(fragment in key_lower for fragment in lowered_fragments) and clean_text(value):
            return clean_text(value)
    return ""


def extract_day(row: Dict[str, str]) -> str:
    # Best source: className like "Friday Flying Squirrels".
    text = " ".join([row.get("className", ""), row.get("program", ""), row.get("enrolledDays", "")])
    for day in DAY_NAMES:
        if re.search(rf"\b{day}\b", text, re.IGNORECASE):
            return day
    return first_matching_column(row, FIELD_FRAGMENTS["day_of_week"])


def extract_group(row: Dict[str, str], known_days: bool = True) -> str:
    class_name = clean_text(row.get("className", ""))
    if class_name:
        # Weekly classes often use "Friday Flying Squirrels". Remove the weekday.
        parts = class_name.split()
        if known_days and parts and parts[0] in DAY_NAMES:
            return " ".join(parts[1:])
        return class_name

    # Fallbacks for program text like "... | Friday | Red Foxes (8-10 years old)".
    program = clean_text(row.get("program", ""))
    if program:
        chunks = [clean_text(chunk) for chunk in program.split("|") if clean_text(chunk)]
        for chunk in reversed(chunks):
            without_age = re.sub(r"\([^)]*\)", "", chunk).strip()
            if without_age and without_age not in DAY_NAMES and "nature school" not in without_age.lower():
                return without_age

    return first_matching_column(row, FIELD_FRAGMENTS["age_group"])


def extract_week_number(row: Dict[str, str], week_key: Dict[str, str]) -> str:
    text = " ".join(str(value) for value in row.values()).lower()
    for date_phrase, week_label in week_key.items():
        if date_phrase.lower() in text:
            return week_label
    # Also handles already-entered week labels like "Week 3".
    match = re.search(r"\bweek\s*(\d+)\b", text, re.IGNORECASE)
    if match:
        return f"Week {match.group(1)}"
    return ""


def extract_when(row: Dict[str, str]) -> str:
    # Outdoor Discovery may store this as day/weekend/custom question/program text.
    direct = first_matching_column(row, FIELD_FRAGMENTS["day_of_week"])
    if direct:
        return direct
    day = extract_day(row)
    if day:
        return day
    return clean_text(row.get("enrolledDays", ""))


def normalize_photo_consent(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    low = value.lower()
    if "not" in low or "no" == low or "face not" in low:
        return "Face not allowed"
    if "allow" in low or "yes" == low:
        return "Allowed"
    return value


# -----------------------------
# Conversion core
# -----------------------------

def convert_enrollsy_rows(
    enrollsy_rows: List[Dict[str, str]],
    sheet_type: str,
    semester: str = "",
    week_key: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    if sheet_type not in OUTPUT_COLUMNS_BY_TYPE:
        raise ValueError(f"Unknown sheet_type: {sheet_type}")

    week_key = week_key or DEFAULT_WEEK_KEY
    output_rows: List[Dict[str, str]] = []

    for row in enrollsy_rows:
        active = clean_text(row.get("active", "")).lower()
        if active and active not in {"true", "yes", "1", "active"}:
            continue

        out = {
            "className": clean_text(row.get("className")),
            "first_name": clean_text(row.get("enrolleeFirstName")),
            "last_name": clean_text(row.get("enrolleeLastName")),
            "middle_name": clean_text(row.get("enrolleeMiddleName")),
            "birthdate": normalize_date(row.get("enrolleeBirthdate")),
            "primary_first_name": clean_text(row.get("accountFirstName")),
            "primary_last_name": clean_text(row.get("accountLastName")),
            "primary_phone": normalize_phone(row.get("accountCellPhone")),
            "secondary_first_name": clean_text(row.get("secondAccountHolderFirstName")),
            "secondary_last_name": clean_text(row.get("secondAccountHolderLastName")),
            "secondary_phone": normalize_phone(row.get("secondAccountHolderCellPhone")),
            "allergies": first_matching_column(row, FIELD_FRAGMENTS["allergies"]),
            "photo_consent": normalize_photo_consent(first_matching_column(row, FIELD_FRAGMENTS["photo_consent"])),
        }

        if sheet_type == "weekly":
            out["Day"] = extract_day(row)
            out["Group"] = extract_group(row, known_days=True)
            out["Semester"] = semester
        elif sheet_type == "summer_camp":
            out["Group"] = extract_group(row, known_days=False)
            out["week_number"] = extract_week_number(row, week_key)
        elif sheet_type == "outdoor_discovery":
            out["Group"] = extract_group(row, known_days=False)
            out["When"] = extract_when(row)

        output_rows.append(out)

    return output_rows


def remove_duplicates(
    new_rows: List[Dict[str, str]], existing_rows: Optional[List[Dict[str, str]]] = None
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Remove duplicates inside the new rows and against optional existing rows."""
    existing_rows = existing_rows or []

    exact_existing = {duplicate_key(row) for row in existing_rows if any(duplicate_key(row))}
    name_existing = {fallback_name_key(row) for row in existing_rows if any(fallback_name_key(row))}

    kept: List[Dict[str, str]] = []
    removed: List[Dict[str, str]] = []
    seen_exact = set()
    seen_names = set()

    for row in new_rows:
        exact = duplicate_key(row)
        name = fallback_name_key(row)

        is_duplicate = False
        reason = ""

        if exact[2] and exact in exact_existing:
            is_duplicate = True
            reason = "matched existing first + last + birthdate"
        elif not exact[2] and name in name_existing:
            is_duplicate = True
            reason = "matched existing first + last"
        elif exact[2] and exact in seen_exact:
            is_duplicate = True
            reason = "duplicate within uploaded Enrollsy file"
        elif not exact[2] and name in seen_names:
            is_duplicate = True
            reason = "duplicate name within uploaded Enrollsy file"

        if is_duplicate:
            removed_row = dict(row)
            removed_row["duplicate_reason"] = reason
            removed.append(removed_row)
            continue

        kept.append(row)
        if exact[2]:
            seen_exact.add(exact)
        seen_names.add(name)

    return kept, removed


def sort_rows(rows: List[Dict[str, str]], sheet_type: str) -> List[Dict[str, str]]:
    if sheet_type == "weekly":
        return sorted(rows, key=lambda r: (r.get("Day", ""), r.get("Group", ""), r.get("first_name", ""), r.get("last_name", "")))
    if sheet_type == "summer_camp":
        return sorted(rows, key=lambda r: (r.get("week_number", ""), r.get("Group", ""), r.get("first_name", ""), r.get("last_name", "")))
    return sorted(rows, key=lambda r: (r.get("When", ""), r.get("Group", ""), r.get("first_name", ""), r.get("last_name", "")))


# -----------------------------
# Streamlit UI
# -----------------------------

def run_app() -> None:
    if st is None:
        raise RuntimeError("Streamlit is not installed. Run: pip install streamlit")

    st.set_page_config(page_title="Enrollsy Form Converter", page_icon="📋", layout="wide")
    st.title("Enrollsy Form Converter")
    st.caption("Convert raw Enrollsy exports into website-ready Weekly, Summer Camp, or Outdoor Discovery CSVs.")

    with st.sidebar:
        sheet_type_label = st.selectbox(
            "Output type",
            ["Weekly", "Summer Camp", "Outdoor Discovery"],
            index=0,
        )
        sheet_type = {
            "Weekly": "weekly",
            "Summer Camp": "summer_camp",
            "Outdoor Discovery": "outdoor_discovery",
        }[sheet_type_label]

        semester = ""
        if sheet_type == "weekly":
            semester = st.text_input("Semester value", value="Spring 2026")

        st.markdown("---")
        st.write("**Summer week key**")
        st.caption("Edit these only if your summer camp dates/weeks change.")
        week_key_text = st.text_area(
            "One per line: date phrase = Week #",
            value="\n".join(f"{k} = {v}" for k, v in DEFAULT_WEEK_KEY.items()),
            height=180,
        )

    enrollsy_file = st.file_uploader("Upload raw Enrollsy CSV", type=["csv"])
    existing_file = st.file_uploader("Optional: upload existing names/formatted CSV to remove duplicates", type=["csv"])

    if not enrollsy_file:
        st.info("Upload an Enrollsy CSV to start.")
        return

    week_key = {}
    for line in week_key_text.splitlines():
        if "=" in line:
            left, right = line.split("=", 1)
            week_key[clean_text(left).lower()] = clean_text(right)
    if not week_key:
        week_key = DEFAULT_WEEK_KEY

    enrollsy_rows = read_csv_upload(enrollsy_file)
    existing_rows = read_csv_upload(existing_file) if existing_file else []

    converted = convert_enrollsy_rows(enrollsy_rows, sheet_type=sheet_type, semester=semester, week_key=week_key)
    kept, removed = remove_duplicates(converted, existing_rows)
    kept = sort_rows(kept, sheet_type)

    fieldnames = OUTPUT_COLUMNS_BY_TYPE[sheet_type]
    removed_fieldnames = fieldnames + ["duplicate_reason"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Enrollsy rows read", len(enrollsy_rows))
    col2.metric("Output rows", len(kept))
    col3.metric("Duplicates removed", len(removed))

    st.subheader("Preview")
    st.dataframe(kept[:100], use_container_width=True)

    clean_csv = rows_to_csv_bytes(kept, fieldnames)
    st.download_button(
        "Download cleaned website-ready CSV",
        data=clean_csv,
        file_name=f"cleaned_{sheet_type}.csv",
        mime="text/csv",
    )

    if removed:
        st.subheader("Removed duplicates")
        st.dataframe(removed[:100], use_container_width=True)
        duplicate_csv = rows_to_csv_bytes(removed, removed_fieldnames)
        st.download_button(
            "Download duplicate report",
            data=duplicate_csv,
            file_name=f"duplicates_removed_{sheet_type}.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    run_app()
