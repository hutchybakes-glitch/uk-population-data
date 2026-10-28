# UK Population Watch - copies the two ONS workbooks the report needs into small CSV files in this repo:
#   data/ons_long_run.csv    the UK from 1851 and each nation before 1991 (ONS "UK population estimates, 1838 to <latest>")
#   data/ons_components.csv  births, deaths and migration for every UK council from mid-2011 (ONS MYEB3)
# Why: ons.gov.uk sits behind Cloudflare, which refuses Power BI's web requests (HTTP 403) in Desktop and the Service,
# so the report reads these copies from GitHub instead. Both workbooks are published once a year (usually June/July):
# re-run this script after each release, then commit and push.
# Licence: OGL v3. Source: Office for National Statistics.
# Usage:  python scripts/make_ons_tables.py

# Import csv to write correctly quoted rows
import csv
# Import path handling
from pathlib import Path
# Import openpyxl to read the Excel workbooks
import openpyxl
# Import requests to call the ONS website
import requests

# The data repo is the folder above this script
REPO = Path(__file__).resolve().parent.parent
# Downloads are kept here between runs (ignored by git)
CACHE = REPO / "scripts" / ".cache"
# The CSV files are written here
OUT = REPO / "data"
# The ONS website
ONS = "https://www.ons.gov.uk"
# The dataset that holds both workbooks
DATASET = "/peoplepopulationandcommunity/populationandmigration/populationestimates/datasets/populationestimatesforukenglandandwalesscotlandandnorthernireland"
# Long-run workbook sheets and the area each one covers
LONG_RUN_SHEETS = {"Table 1": "K02000001", "Table 10": "E92000001", "Table 12": "W92000004", "Table 14": "S92000003", "Table 17": "N92000002"}
# The MYEB3 measures the report uses, in output order
MEASURES = ["population", "births", "deaths", "internal_net", "international_in", "international_out", "international_net", "other_change"]
# One web session for every call, naming the project as the client
WEB = requests.Session()
# The client name sent with each request
WEB.headers["User-Agent"] = "UK Population Watch data script (github.com/swhutchinson/uk-population-data)"


# Read one ONS page as JSON
def get_json(path):
    # Ask for the page's JSON form
    reply = WEB.get(ONS + path + "/data", timeout=60)
    # Stop on any error status
    reply.raise_for_status()
    # Hand back the parsed JSON
    return reply.json()


# Find the newest edition whose address contains the prefix, download its file (cached) and return the path and edition name
def latest_file(prefix):
    # The dataset page lists every edition, newest first
    editions = [d["uri"] for d in get_json(DATASET)["datasets"]]
    # The newest edition of the series we want
    edition = next(u for u in editions if DATASET + prefix in u)
    # The edition page names its download file
    file_name = get_json(edition)["downloads"][0]["file"]
    # Where the download is cached
    local = CACHE / file_name
    # Download only if not already cached
    if not local.exists():
        # Make sure the cache folder exists
        CACHE.mkdir(parents=True, exist_ok=True)
        # Say what is being downloaded
        print("Downloading", file_name)
        # Stream the file (the detailed tables are about 50 MB)
        reply = WEB.get(ONS + "/file", params={"uri": edition + "/" + file_name}, timeout=600)
        # Stop on any error status
        reply.raise_for_status()
        # Save it
        local.write_bytes(reply.content)
    # The edition's short name, e.g. "mid2011tomid2025"
    return local, edition.rsplit("/", 1)[-1]


# Write a list of dictionaries to a CSV file in the data folder
def write(name, columns, rows):
    # Make sure the data folder exists
    OUT.mkdir(exist_ok=True)
    # Open the file with Unix line endings so git diffs stay clean
    with open(OUT / name, "w", encoding="utf-8", newline="") as f:
        # A writer for the chosen columns
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        # Header row
        w.writeheader()
        # Every data row
        w.writerows(rows)
    # Report the row count
    print(f"{name}: {len(rows):,} rows")


# Long run: years before 1991 (Nomis covers 1991 onwards) for the UK and each nation
def long_run():
    # The newest long-run workbook
    path, edition = latest_file("/ukpopulationestimates1838to")
    # Open it read-only with cell values rather than formulas
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    # Collected rows
    rows = []
    # Each sheet and the area it covers
    for sheet, code in LONG_RUN_SHEETS.items():
        # Each row's first two cells: the year label and the total population
        for label, total, *_ in book[sheet].iter_rows(values_only=True):
            # Keep rows labelled "Mid-YYYY" that have a total
            if isinstance(label, str) and label.startswith("Mid-") and total not in (None, ""):
                # The year number
                year = int(label.split("-")[1])
                # Only the years before Nomis takes over
                if year < 1991:
                    # One output row
                    rows.append({"Year": year, "AreaCode": code, "Population": int(round(float(total))), "Edition": edition})
    # Save
    write("ons_long_run.csv", ["Year", "AreaCode", "Population", "Edition"], rows)


# Components of change: one row per council and year, one column per measure
def components():
    # The newest UK detailed tables workbook
    path, edition = latest_file("/mid2011tomid")
    # Open it read-only with cell values rather than formulas
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    # The rows of the MYEB3 sheet
    sheet_rows = book["MYEB3"].iter_rows(values_only=True)
    # Skip the title rows down to the header, which starts "ladcode23"
    header = next(r for r in sheet_rows if r and r[0] == "ladcode23")
    # Collected values, keyed by council and year
    table = {}
    # Each council row below the header
    for r in sheet_rows:
        # Council rows have a 9-character code
        if not (isinstance(r[0], str) and len(r[0]) == 9):
            # Skip notes and blank rows
            continue
        # Each "<measure>_<year>" cell in the row
        for name, value in zip(header, r):
            # Split off the year from the end of the column name
            measure, _, year = str(name).rpartition("_")
            # Only the measures we use, with a real year
            if measure in MEASURES and year.isdigit() and value not in (None, ""):
                # The output row for this council and year
                row = table.setdefault((r[0], int(year)), {"ladcode23": r[0], "Year": int(year), "Edition": edition})
                # Store the value as a whole number
                row[measure] = int(round(float(value)))
    # Rows in a fixed order
    rows = [table[k] for k in sorted(table)]
    # Save
    write("ons_components.csv", ["ladcode23", "Year"] + MEASURES + ["Edition"], rows)


# Run both when the script is started directly
if __name__ == "__main__":
    # The long-run series
    long_run()
    # The components of change
    components()
