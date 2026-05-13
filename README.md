# Enrollsy Form Converter v2

This Streamlit app converts raw Enrollsy CSV exports into clean CSVs for upload.

## Supports

- **Weekly**: `Day`, `Group`, `Semester`
- **Outdoor Discovery**: `semester`, `group`, `session`
- **Summer Camp**: `Group`, `week_numbers` with multiple weeks merged into one row per child/group
- **Monthly**: `cohort`, `year`

## Formatting rules

- Birthdates become `YYYY-MM-DD`
- Phone numbers become flat 10-digit numbers like `1234567890`
- Leading `1` country code is removed from phone numbers
- Photo consent becomes `Allowed` or `Face not allowed`
- Optional existing CSV upload removes kids already in your current sheet
- Optional template CSV upload preserves exact column order from one of your corrected files

## Run

```bash
pip install -r requirements.txt
streamlit run enrollsy_form_converter_app_v2.py
```

## Replit

Upload these files to Replit:

- `enrollsy_form_converter_app_v2.py`
- `requirements.txt`

Then run:

```bash
streamlit run enrollsy_form_converter_app_v2.py --server.port 3000
```
