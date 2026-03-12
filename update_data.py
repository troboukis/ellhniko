import requests
import pandas as pd
import pdfplumber
import os
import re
import time
import json
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Configuration ---
DIAVGEIA_API_BASE = "https://diavgeia.gov.gr/luminapi/api/search/export"
START_DATE = date(2019, 1, 1)
TERMS = ["Α-Π1", "Α-Π2", "Α-Π3", "Α-Π4", "Α-Π5", "Α-Π6", "ΠΜ-Π1", "Α-Α1-1", "ΠΜ Α1", "ΠΜ Α2"]

ALL_CSV = "ellhniko_all.csv"
ADEIES_CSV = "oikodomikes_adeies.csv"
OIKOPEDA_CSV = "oikopeda.csv"
PERMITS_CSV = "permits_ellhniko.csv"
LAST_SEARCH_JSON = "last_search.json"
PDF_DIR = "documents/oik_adeies"
DISPLAY_TZ = ZoneInfo("Europe/Athens")


# ---------------------------------------------------------------------------
# Diavgeia API
# ---------------------------------------------------------------------------

def fetch_range(start: date, end: date) -> list:
    url = (
        f"{DIAVGEIA_API_BASE}"
        f"?q=q:%22%CE%B5%CE%BB%CE%BB%CE%B7%CE%BD%CE%B9%CE%BA%CE%BF%22"
        f"&fq=thematicCategory:%22%CF%80%CE%B5%CF%81%CE%B9%CE%B2%CE%B1%CE%BB%CE%BB%CE%BF%CE%BD%22"
        f"&fq=organizationUid:99201077"
        f"&fq=unitUid:77540"
        f"&decisionTypeUid:2.4.6.1"
        f"&fq=submissionTimestamp:[DT({start}T00:01:00)%20TO%20DT({end}T00:00:00)]"
        f"&fq=issueDate:[DT({start}T00:01:00)%20TO%20DT({end}T00:00:00)]"
        f"&wt=json"
    )
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json().get("decisionResultList", [])


def fetch_all(start: date, end: date) -> list:
    results = []
    current = start
    while current < end:
        chunk_end = min(current + relativedelta(months=6), end)
        print(f"  Fetching {current} → {chunk_end} ...", flush=True)
        try:
            chunk = fetch_range(current, chunk_end)
            results.extend(chunk)
            print(f"    Got {len(chunk)} records (total: {len(results)})")
        except Exception as e:
            print(f"    WARNING: failed chunk {current}→{chunk_end}: {e}")
        current += relativedelta(months=6)
        time.sleep(0.5)
    return results


def get_last_date(csv_path: str) -> date:
    if not os.path.exists(csv_path):
        return START_DATE
    try:
        df = pd.read_csv(csv_path, usecols=lambda c: c in ("issueDate", "submissionTimestamp"))
        for col in ("issueDate", "submissionTimestamp"):
            if col in df.columns:
                latest = pd.to_datetime(df[col], errors="coerce").max()
                if pd.notna(latest):
                    return latest.date()
    except Exception as e:
        print(f"  Could not read {csv_path}: {e}")
    return START_DATE


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

def get_already_downloaded() -> set:
    """Return set of ADA codes that already have a local PDF."""
    if not os.path.isdir(PDF_DIR):
        os.makedirs(PDF_DIR, exist_ok=True)
        return set()
    return {os.path.splitext(f)[0] for f in os.listdir(PDF_DIR) if f.endswith(".pdf")}


def download_new_pdfs(adeies_df: pd.DataFrame) -> list:
    """Download PDFs for any permits not yet on disk. Returns list of new PDF paths."""
    already = get_already_downloaded()
    new_paths = []
    total = len(adeies_df)

    for i, row in enumerate(adeies_df.itertuples(), 1):
        ada = str(row.ada)
        if ada in already:
            continue
        try:
            doc_url = row.documentUrl
            # Skip if the doc is already represented by ADA on disk
            doc_id = re.search(r"doc/(.*)", doc_url)
            if doc_id and doc_id.group(1) in already:
                continue

            print(f"  Downloading PDF {i}/{total}: {ada}")
            resp = requests.get(doc_url, timeout=60)
            resp.raise_for_status()
            pdf_path = os.path.join(PDF_DIR, f"{ada}.pdf")
            with open(pdf_path, "wb") as f:
                f.write(resp.content)
            new_paths.append(pdf_path)
            with open("download_report.log", "a", encoding="utf-8") as log:
                log.write(f"[{datetime.now().isoformat()}] {ada} downloaded\n")
            time.sleep(1)
        except Exception as e:
            print(f"    WARNING: could not download {ada}: {e}")
            with open("download_report.log", "a", encoding="utf-8") as log:
                log.write(f"[{datetime.now().isoformat()}] {ada} FAILED: {e}\n")

    return new_paths


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: str) -> list:
    """Extract all tables from a PDF file."""
    tables = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    tables.append(table)
    except Exception as e:
        print(f"    WARNING: could not parse {pdf_path}: {e}")
    return tables


def find_value(tables, key):
    for table in tables:
        for row in table:
            if row and len(row) >= 2 and row[0]:
                clean_key = row[0].replace("\n", " ").strip()
                if key.lower() in clean_key.lower():
                    return row[1].replace("\n", " ").strip() if row[1] else None
    return None


def find_coverage_value(tables, key, column_idx=4):
    for table in tables:
        for row in table:
            if row and len(row) >= column_idx + 1 and row[0]:
                clean_key = row[0].replace("\n", " ").strip()
                if key.lower() in clean_key.lower():
                    for idx in [column_idx, 3, 2, 1]:
                        if len(row) > idx and row[idx]:
                            return row[idx].replace("\n", " ").strip()
    return None


def find_owner(tables):
    owners = []
    for table in tables:
        if not (table and len(table) > 1):
            continue
        header = table[0] or []
        if any("κυρίου" in str(cell).lower() for cell in header if cell):
            for row in table[1:]:
                if row and len(row) >= 4 and row[0] and "Επώνυμο" not in str(row[0]):
                    name = row[0].replace("\n", " ").strip()
                    role = row[3].replace("\n", " ").strip() if row[3] else ""
                    if name and "Ιδιοκτήτης" in role:
                        owners.append(name)
    return "; ".join(owners) if owners else None


def find_coordinates(tables):
    for table in tables:
        for row in table:
            if row and len(row) >= 2 and row[0] and "Συντεταγμένες" in str(row[0]):
                return row[1].strip() if row[1] else None
    return None


def tables_to_record(ada: str, tables: list) -> dict:
    return {
        "ada": ada,
        "aa_praxis": find_value(tables, "Α/Α Πράξης"),
        "issue_date": find_value(tables, "Ημ/νία έκδοσης"),
        "valid_until": find_value(tables, "Ισχύει έως"),
        "description": find_value(tables, "Περιγραφή"),
        "address": find_value(tables, "Οδός"),
        "city": find_value(tables, "Πόλη/Οικισμός"),
        "municipality": find_value(tables, "Δήμος"),
        "area": find_value(tables, "Δημοτική Ενότητα"),
        "ot": find_value(tables, "ΟΤ"),
        "kaek": find_value(tables, "ΚΑΕΚ"),
        "permit_type": find_value(tables, "Τύπος Πράξης"),
        "owner": find_owner(tables),
        "plot_area": find_value(tables, "Εμβαδόν οικοπέδου"),
        "coverage_area": find_coverage_value(tables, "κάλυψης κτιρίου"),
        "building_area": find_coverage_value(tables, "δόμησης κτιρίου"),
        "uncovered_area": find_coverage_value(tables, "ακάλυπτου χώρου"),
        "volume": find_coverage_value(tables, "Όγκος κτιρίου"),
        "max_height": find_coverage_value(tables, "Μέγιστο ύψος"),
        "floors": find_coverage_value(tables, "Αριθμός Ορόφων"),
        "parking_spots": find_coverage_value(tables, "Θέσεων Στάθμευσης"),
        "coordinates": find_coordinates(tables),
    }


def parse_and_clean(pdf_paths: list) -> pd.DataFrame:
    records = []
    for path in pdf_paths:
        ada = os.path.splitext(os.path.basename(path))[0]
        tables = parse_pdf(path)
        if tables:
            records.append(tables_to_record(ada, tables))

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    numeric_cols = ["plot_area", "coverage_area", "building_area", "uncovered_area",
                    "volume", "max_height", "floors", "parking_spots"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = (df[col].str.replace(".", "", regex=False)
                               .str.replace(",", ".", regex=False))
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ("issue_date", "valid_until"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def save_csv(df: pd.DataFrame, path: str, label: str):
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  Saved {path}: {len(df)} records ({label})")


def write_last_search_metadata(search_ts: datetime):
    timestamp = search_ts.astimezone(DISPLAY_TZ)
    payload = {
        "last_search_at": timestamp.isoformat(),
        "display": timestamp.strftime("%Y-%m-%d %H:%M %Z"),
    }
    Path(LAST_SEARCH_JSON).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n  {LAST_SEARCH_JSON} set to {payload['display']}")


def main():
    today = date.today()
    search_started_at = datetime.now(DISPLAY_TZ)

    # --- 1. Fetch new API data ---
    last_date = get_last_date(ALL_CSV)
    fetch_start = max(START_DATE, last_date - relativedelta(days=30))
    print(f"\n=== Fetching Diavgeia data from {fetch_start} to {today} ===")
    new_records = fetch_all(fetch_start, today)

    if not new_records:
        print("No new API records found.")
    else:
        new_df = pd.DataFrame(new_records)

        # Update ellhniko_all.csv
        if os.path.exists(ALL_CSV):
            existing = pd.read_csv(ALL_CSV, dtype=str)
            combined = (pd.concat([existing, new_df.astype(str)], ignore_index=True)
                          .drop_duplicates(subset=["ada"]))
        else:
            combined = new_df

        save_csv(combined, ALL_CSV, "all decisions")

        # Update oikodomikes_adeies.csv
        mask_adeies = (
            combined["subject"].str.contains("μητροπολιτικ", case=False, na=False)
            & combined["subject"].str.contains("ελληνικ", case=False, na=False)
        )
        adeies_df = combined[mask_adeies].reset_index(drop=True)
        save_csv(adeies_df, ADEIES_CSV, "metro pole decisions")

        # Update oikopeda.csv
        mask_oikopeda = combined["subject"].apply(lambda s: any(t in str(s) for t in TERMS))
        save_csv(combined[mask_oikopeda].reset_index(drop=True), OIKOPEDA_CSV, "plot decisions")

    # --- 2. Download new PDFs ---
    print(f"\n=== Downloading new PDFs ===")
    if not os.path.exists(ADEIES_CSV):
        print("  No oikodomikes_adeies.csv found — skipping PDF step.")
        write_last_search_metadata(search_started_at)
        return

    adeies_df = pd.read_csv(ADEIES_CSV, dtype=str)
    new_pdf_paths = download_new_pdfs(adeies_df)
    print(f"  {len(new_pdf_paths)} new PDFs downloaded.")

    # --- 3. Parse new PDFs and update permits_ellhniko.csv ---
    if not new_pdf_paths:
        print("  No new PDFs to parse.")
    else:
        print(f"\n=== Parsing {len(new_pdf_paths)} new PDFs ===")
        new_permits = parse_and_clean(new_pdf_paths)

        if new_permits.empty:
            print("  No data extracted from new PDFs.")
        else:
            if os.path.exists(PERMITS_CSV):
                existing_permits = pd.read_csv(PERMITS_CSV, dtype=str)
                combined_permits = (
                    pd.concat([existing_permits, new_permits.astype(str)], ignore_index=True)
                      .drop_duplicates(subset=["ada"])
                )
            else:
                combined_permits = new_permits

            save_csv(combined_permits, PERMITS_CSV, "parsed permits")

    # --- 4. Write last-search timestamp metadata ---
    write_last_search_metadata(search_started_at)

    print("\nDone.")


if __name__ == "__main__":
    main()
