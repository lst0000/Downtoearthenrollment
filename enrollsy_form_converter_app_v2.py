"""
Enrollsy Form Converter v2
--------------------------
Converts raw Enrollsy CSV exports into website-ready import CSVs.

Supported output types:
- Weekly: first/last/etc + Day, Group, Semester
- Outdoor Discovery: first/last/etc + semester, group, session
- Summer Camp: first/last/etc + Group, week_numbers, with multi-week enrollments merged
- Monthly: first/last/etc + cohort, year

Features:
- Optional existing/template CSV upload for duplicate removal
- Optional template CSV upload to preserve exact column order
- Birthdates normalized to YYYY-MM-DD
- Phone numbers normalized to 10 flat digits, no +1, dashes, spaces, or parentheses
- Photo consent normalized to Allowed / Face not allowed

Run locally or on Replit/Streamlit Cloud:
    pip install streamlit
    streamlit run enrollsy_form_converter_app_v2.py
"""

from __future__ import annotations

import csv
import io
import re
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import streamlit as st
except ImportError:
    st = None

COMMON_OUTPUT_COLUMNS = [
    "first_name", "last_name", "middle_name", "birthdate",
    "primary_first_name", "primary_last_name", "primary_phone",
    "secondary_first_name", "secondary_last_name", "secondary_phone",
    "allergies", "photo_consent",
]

OUTPUT_COLUMNS_BY_TYPE = {
    "weekly": COMMON_OUTPUT_COLUMNS + ["Day", "Group", "Semester"],
    "outdoor_discovery": COMMON_OUTPUT_COLUMNS + ["semester", "group", "session"],
    "summer_camp": COMMON_OUTPUT_COLUMNS + ["Group", "week_numbers"],
    "monthly": COMMON_OUTPUT_COLUMNS + ["cohort", "year"],
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SESSION_NAMES = ["Morning", "Afternoon", "Full Day"]
KNOWN_GROUPS = [
    "Flying Squirrels", "Red Foxes", "Peregrine Falcons",
    "Wild Wonders", "Adventure Scouts", "Outdoor Explorers",
]

DEFAULT_WEEK_KEY = OrderedDict([
    ("june 8", "Week 1"), ("june 15", "Week 2"), ("june 22", "Week 3"),
    ("june 29", "Week 4"), ("july 6", "Week 5"), ("july 13", "Week 6"),
    ("july 20", "Week 7"), ("july 27", "Week 8"), ("august 3", "Week 9"),
])

FIELD_FRAGMENTS = {
    "allergies": ["allerg"],
    "photo_consent": ["photo/video permission", "photographs/video", "photo"],
    "day": ["day of the week", "day of the weekend"],
    "age_group": ["age group"],
}

# ---------------- CSV helpers ----------------

def read_csv_upload(uploaded_file) -> List[Dict[str, str]]:
    content = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(content)))


def read_csv_path(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def rows_to_csv_bytes(rows: List[Dict[str, str]], fieldnames: List[str]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def get_headers_from_upload(uploaded_file) -> List[str]:
    rows = read_csv_upload(uploaded_file)
    return list(rows[0].keys()) if rows else []

# ---------------- cleaning helpers ----------------

def clean_text(value: Optional[str]) -> str:
    return " ".join((value or "").strip().split())


def normalize_name(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_text(value).lower())


def normalize_phone(value: Optional[str]) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # If Enrollsy ever sends extensions or odd formatting, keep the first real US number.
    if len(digits) > 10 and digits.startswith("1"):
        digits = digits[1:11]
    elif len(digits) > 10:
        digits = digits[:10]
    return digits


def normalize_date(value: Optional[str]) -> str:
    value = clean_text(value)
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def first_matching_column(row: Dict[str, str], fragments: Iterable[str]) -> str:
    fragments = [f.lower() for f in fragments]
    for key, value in row.items():
        if clean_text(value) and any(f in key.lower() for f in fragments):
            return clean_text(value)
    return ""


def normalize_photo_consent(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    low = value.lower()
    if "not" in low or low in {"no", "n"} or "face not" in low:
        return "Face not allowed"
    if "allow" in low or low in {"yes", "y"}:
        return "Allowed"
    return value

# ---------------- Enrollsy extraction ----------------

def combined_text(row: Dict[str, str]) -> str:
    priority = ["className", "program", "enrolledDays"]
    return " ".join(clean_text(row.get(k, "")) for k in priority if clean_text(row.get(k, "")))


def extract_day(row: Dict[str, str]) -> str:
    text = combined_text(row)
    for day in DAY_NAMES:
        if re.search(rf"\b{day}\b", text, re.IGNORECASE):
            return day
    return first_matching_column(row, FIELD_FRAGMENTS["day"])


def extract_session(row: Dict[str, str]) -> str:
    text = combined_text(row)
    for session in SESSION_NAMES:
        if re.search(rf"\b{re.escape(session)}\b", text, re.IGNORECASE):
            return session
    # Time-based fallback for Outdoor Discovery when Enrollsy changes naming.
    if re.search(r"12\s*:?30|afternoon", text, re.IGNORECASE):
        return "Afternoon"
    if re.search(r"9\s*:?00|morning", text, re.IGNORECASE):
        return "Morning"
    return ""


def extract_known_group(text: str) -> str:
    for group in KNOWN_GROUPS:
        if re.search(rf"\b{re.escape(group)}\b", text, re.IGNORECASE):
            return group
    return ""


def extract_group(row: Dict[str, str]) -> str:
    text = combined_text(row)
    known = extract_known_group(text)
    if known:
        return known

    class_name = clean_text(row.get("className", ""))
    if class_name:
        # Remove common prefixes: weekday, session, or summer date phrase.
        value = class_name
        value = re.sub(rf"^({'|'.join(DAY_NAMES)})\s+", "", value, flags=re.I)
        value = re.sub(rf"^({'|'.join(SESSION_NAMES)})\s+", "", value, flags=re.I)
        value = re.sub(r"^(june|july|august)\s+\d+(st|nd|rd|th)?\s+", "", value, flags=re.I)
        return clean_text(value)

    program = clean_text(row.get("program", ""))
    if program:
        chunks = [clean_text(c) for c in program.split("|") if clean_text(c)]
        for chunk in reversed(chunks):
            chunk = re.sub(r"\([^)]*\)", "", chunk).strip()
            if chunk and chunk not in DAY_NAMES and not any(s.lower() == chunk.lower() for s in SESSION_NAMES):
                if "nature school" not in chunk.lower() and "summer camp" not in chunk.lower():
                    return chunk
    return first_matching_column(row, FIELD_FRAGMENTS["age_group"])


def extract_week_numbers(row: Dict[str, str], week_key: Dict[str, str]) -> List[str]:
    text = " ".join(str(value) for value in row.values()).lower()
    found: List[str] = []
    for phrase, week in week_key.items():
        if phrase.lower() in text and week not in found:
            found.append(week)
    for match in re.findall(r"\bweek\s*(\d+)\b", text, flags=re.I):
        label = f"Week {int(match)}"
        if label not in found:
            found.append(label)
    return found


def base_output_row(row: Dict[str, str]) -> Dict[str, str]:
    return {
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

# ---------------- conversion ----------------

def is_active(row: Dict[str, str]) -> bool:
    active = clean_text(row.get("active", "")).lower()
    return not active or active in {"true", "yes", "1", "active"}


def convert_enrollsy_rows(
    enrollsy_rows: List[Dict[str, str]],
    sheet_type: str,
    semester: str = "Spring 2026",
    year: str = "2025 - 2026",
    week_key: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    if sheet_type not in OUTPUT_COLUMNS_BY_TYPE:
        raise ValueError(f"Unknown sheet_type: {sheet_type}")
    week_key = week_key or DEFAULT_WEEK_KEY

    rows: List[Dict[str, str]] = []
    for raw in enrollsy_rows:
        if not is_active(raw):
            continue
        out = base_output_row(raw)
        if sheet_type == "weekly":
            out.update({"Day": extract_day(raw), "Group": extract_group(raw), "Semester": semester})
        elif sheet_type == "outdoor_discovery":
            out.update({"semester": semester, "group": extract_group(raw), "session": extract_session(raw)})
        elif sheet_type == "summer_camp":
            out.update({"Group": extract_group(raw), "week_numbers": ", ".join(extract_week_numbers(raw, week_key))})
        elif sheet_type == "monthly":
            out.update({"cohort": extract_day(raw), "year": year})
        rows.append(out)

    if sheet_type == "summer_camp":
        rows = merge_summer_weeks(rows)
    return rows


def person_key(row: Dict[str, str]) -> Tuple[str, str, str]:
    return (
        normalize_name(row.get("first_name") or row.get("enrolleeFirstName")),
        normalize_name(row.get("last_name") or row.get("enrolleeLastName")),
        normalize_date(row.get("birthdate") or row.get("enrolleeBirthdate")),
    )


def name_key(row: Dict[str, str]) -> Tuple[str, str]:
    return (normalize_name(row.get("first_name") or row.get("enrolleeFirstName")), normalize_name(row.get("last_name") or row.get("enrolleeLastName")))


def merge_summer_weeks(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged: OrderedDict[Tuple[str, str, str, str], Dict[str, str]] = OrderedDict()
    for row in rows:
        key = person_key(row) + (clean_text(row.get("Group")),)
        weeks = [clean_text(w) for w in row.get("week_numbers", "").split(",") if clean_text(w)]
        if key not in merged:
            merged[key] = dict(row)
            merged[key]["week_numbers"] = ""
        existing = [clean_text(w) for w in merged[key].get("week_numbers", "").split(",") if clean_text(w)]
        for week in weeks:
            if week and week not in existing:
                existing.append(week)
        merged[key]["week_numbers"] = ", ".join(existing)
    return list(merged.values())


def remove_duplicates(new_rows: List[Dict[str, str]], existing_rows: Optional[List[Dict[str, str]]] = None) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    existing_rows = existing_rows or []
    exact_existing = {person_key(r) for r in existing_rows if any(person_key(r))}
    name_existing = {name_key(r) for r in existing_rows if any(name_key(r))}
    kept: List[Dict[str, str]] = []
    removed: List[Dict[str, str]] = []
    seen_exact = set()

    for row in new_rows:
        exact = person_key(row)
        names = name_key(row)
        reason = ""
        if exact[2] and exact in exact_existing:
            reason = "matched existing first + last + birthdate"
        elif not exact[2] and names in name_existing:
            reason = "matched existing first + last"
        elif exact[2] and exact in seen_exact:
            reason = "duplicate inside this cleaned upload"

        if reason:
            duplicate = dict(row)
            duplicate["duplicate_reason"] = reason
            removed.append(duplicate)
        else:
            kept.append(row)
            if exact[2]:
                seen_exact.add(exact)
    return kept, removed


def sort_rows(rows: List[Dict[str, str]], sheet_type: str) -> List[Dict[str, str]]:
    if sheet_type == "weekly":
        day_order = {d: i for i, d in enumerate(DAY_NAMES)}
        return sorted(rows, key=lambda r: (day_order.get(r.get("Day", ""), 99), r.get("Group", ""), r.get("first_name", ""), r.get("last_name", "")))
    if sheet_type == "outdoor_discovery":
        session_order = {"Morning": 0, "Afternoon": 1, "Full Day": 2}
        return sorted(rows, key=lambda r: (session_order.get(r.get("session", ""), 99), r.get("group", ""), r.get("first_name", ""), r.get("last_name", "")))
    if sheet_type == "summer_camp":
        return sorted(rows, key=lambda r: (r.get("Group", ""), r.get("first_name", ""), r.get("last_name", "")))
    return sorted(rows, key=lambda r: (r.get("cohort", ""), r.get("first_name", ""), r.get("last_name", "")))

# ---------------- app ----------------

def run_app() -> None:
    if st is None:
        raise RuntimeError("Streamlit is not installed. Run: pip install streamlit")

    st.set_page_config(page_title="Enrollsy Form Converter", page_icon="📋", layout="wide")
    st.title("Enrollsy Form Converter")
    st.caption("Upload a raw Enrollsy CSV, choose the program type, remove existing kids if needed, and download the website-ready CSV.")

    label_to_type = {
        "Weekly": "weekly",
        "Outdoor Discovery": "outdoor_discovery",
        "Summer Camp": "summer_camp",
        "Monthly": "monthly",
    }

    with st.sidebar:
        selected = st.selectbox("Output type", list(label_to_type.keys()))
        sheet_type = label_to_type[selected]
        semester = st.text_input("Semester", value="Spring 2026")
        year = st.text_input("Year", value="2025 - 2026")
        st.markdown("---")
        st.write("**Summer week key**")
        week_key_text = st.text_area(
            "One per line: date phrase = Week #",
            value="\n".join(f"{k} = {v}" for k, v in DEFAULT_WEEK_KEY.items()),
            height=180,
        )

    enrollsy_file = st.file_uploader("Upload raw Enrollsy CSV", type=["csv"])
    existing_file = st.file_uploader("Optional: upload existing cleaned CSV to remove kids already enrolled", type=["csv"])
    template_file = st.file_uploader("Optional: upload corrected/template CSV to preserve its exact column order", type=["csv"])

    if not enrollsy_file:
        st.info("Upload a raw Enrollsy CSV to start.")
        return

    week_key: Dict[str, str] = OrderedDict()
    for line in week_key_text.splitlines():
        if "=" in line:
            left, right = line.split("=", 1)
            week_key[clean_text(left).lower()] = clean_text(right)
    if not week_key:
        week_key = DEFAULT_WEEK_KEY

    enrollsy_rows = read_csv_upload(enrollsy_file)
    existing_rows = read_csv_upload(existing_file) if existing_file else []

    converted = convert_enrollsy_rows(enrollsy_rows, sheet_type, semester=semester, year=year, week_key=week_key)
    kept, removed = remove_duplicates(converted, existing_rows)
    kept = sort_rows(kept, sheet_type)

    fieldnames = OUTPUT_COLUMNS_BY_TYPE[sheet_type]
    if template_file:
        template_rows = read_csv_upload(template_file)
        if template_rows:
            fieldnames = list(template_rows[0].keys())

    col1, col2, col3 = st.columns(3)
    col1.metric("Enrollsy rows read", len(enrollsy_rows))
    col2.metric("Output rows", len(kept))
    col3.metric("Duplicates removed", len(removed))

    st.subheader("Preview")
    st.dataframe(kept[:200], use_container_width=True)

    st.download_button(
        "Download cleaned website-ready CSV",
        data=rows_to_csv_bytes(kept, fieldnames),
        file_name=f"cleaned_{sheet_type}.csv",
        mime="text/csv",
    )

    if removed:
        st.subheader("Removed duplicates")
        removed_fieldnames = fieldnames + ["duplicate_reason"]
        st.dataframe(removed[:200], use_container_width=True)
        st.download_button(
            "Download duplicate report",
            data=rows_to_csv_bytes(removed, removed_fieldnames),
            file_name=f"duplicates_removed_{sheet_type}.csv",
            mime="text/csv",
        )

if __name__ == "__main__":
    run_app()
