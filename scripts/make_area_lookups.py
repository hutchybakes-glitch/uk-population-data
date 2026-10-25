# UK Population Watch - builds the place search index and the area lookups the Power BI model reads from GitHub.
# Every live UK postcode (ONS Postcode Directory) carries its council, county, region, nation and 2024 built-up area,
# so councils and towns are nested by counting postcodes, and each searchable place gets its council and town.
# Outputs (read by Power BI from raw.githubusercontent.com):
#   reference/place_index.csv.gz   the UK Fuel Watch place index plus CouncilCode and TownCode
#   reference/council_lookup.csv   council -> county or unitary, region, nation, centre and land area
#   reference/town_lookup.csv      2024 built-up area -> council, centre (mean of its postcodes) and area
#   reference/council_code_map.csv older or newer council codes -> the April 2023 codes the population data uses
# Licence: OGL v3. Contains OS data (c) Crown copyright and database right; Contains Royal Mail data
# (c) Royal Mail copyright and database right; Source: Office for National Statistics.
# Usage:  python scripts/make_area_lookups.py    (re-run yearly; delete scripts/.cache to force a fresh download)

# Import gzip to read and write compressed files
import gzip
# Import csv to read and write correctly quoted rows
import csv
# Import json to read and write the download cache
import json
# Import math for the cosine in the distance formula
import math
# Import time to pause between retries
import time
# Import path handling
from pathlib import Path
# Import counting and grouping helpers
from collections import Counter, defaultdict
# Import a thread pool to download a few page ranges at once
from concurrent.futures import ThreadPoolExecutor
# Import requests to call the ONS ArcGIS services
import requests

# The data repo is the folder above this script
REPO = Path(__file__).resolve().parent.parent
# The output folder inside the data repo
OUTDIR = REPO / "reference"
# Make sure it exists
OUTDIR.mkdir(parents=True, exist_ok=True)
# The UK Fuel Watch place index this script extends
FUEL_INDEX = Path.home() / "Documents" / "uk-fuel-data" / "reference" / "place_index.csv.gz"
# The ArcGIS REST root that hosts the ONS datasets
ONS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
# The ONS Postcode Directory centroid layer
ONSPD = f"{ONS}/ONSPD_Online_latest_Postcode_Centroids/FeatureServer/0/query"
# Council boundaries at full resolution (for land areas) and ultra-generalised (for centres)
LAD_BFC = f"{ONS}/Local_Authority_Districts_May_2026_Boundaries_UK_BFC/FeatureServer/0/query"
# The 2024 built-up area layer (England and Wales), which carries each town's area in hectares
BUA = f"{ONS}/main_ONS_BUA_2024_EW_V2/FeatureServer/0/query"
# Local cache of the slow postcode download, so re-runs are quick
CACHE = REPO / "scripts" / ".cache" / "onspd_uk_postcodes_bua.json.gz"
# Number of page ranges downloaded at the same time (kept small to be polite)
STREAMS = 3

# Councils whose code changed after April 2023 (Barnsley and Sheffield, 2026 boundary change) -> the April 2023 code
NEWER_TO_2023 = {"E08000038": "E08000016", "E08000039": "E08000019"}
# April 2021 district codes merged into new unitary councils in April 2023 (the 2022-based projections use 2021 codes)
OLDER_TO_2023 = {
    # Allerdale, Carlisle and Copeland became Cumberland
    "E07000026": "E06000063", "E07000028": "E06000063", "E07000029": "E06000063",
    # Barrow-in-Furness, Eden and South Lakeland became Westmorland and Furness
    "E07000027": "E06000064", "E07000030": "E06000064", "E07000031": "E06000064",
    # Craven, Hambleton, Harrogate, Richmondshire, Ryedale, Scarborough and Selby became North Yorkshire
    "E07000163": "E06000065", "E07000164": "E06000065", "E07000165": "E06000065", "E07000166": "E06000065",
    "E07000167": "E06000065", "E07000168": "E06000065", "E07000169": "E06000065",
    # Mendip, Sedgemoor, South Somerset and Somerset West and Taunton became Somerset
    "E07000187": "E06000066", "E07000188": "E06000066", "E07000189": "E06000066", "E07000246": "E06000066",
}
# Nation codes for the one-letter prefix of every area code
NATION_OF = {"E": "E92000001", "W": "W92000004", "S": "S92000003", "N": "N92000002"}


# A GET helper that retries temporary failures
def get(url, params):
    # Try up to five times
    for attempt in range(5):
        # Attempt the request
        try:
            # Perform the request with a generous timeout
            r = requests.get(url, params=params, timeout=180)
            # Return the parsed JSON when the server answered normally without an error body
            if r.status_code == 200 and "error" not in r.json():
                # Hand back the JSON body
                return r.json()
        # Treat connection problems as temporary
        except requests.exceptions.RequestException:
            # Ignore and retry
            pass
        # Wait a little longer after each failure
        time.sleep(10 * (attempt + 1))
    # Give up loudly after five failures
    raise RuntimeError(f"Failed after retries: {url} {params}")


# Fetch every row of an ArcGIS layer without geometry, paging by offset
def all_rows(url, fields, where="1=1"):
    # Rows collected so far
    out = []
    # Keep fetching until a short page comes back
    while True:
        # Ask for the next 2,000 rows
        page = get(url, {"where": where, "outFields": fields, "returnGeometry": "false", "resultOffset": len(out), "resultRecordCount": 2000, "orderByFields": "OBJECTID" if "OBJECTID" in fields else fields.split(",")[0], "f": "json"})
        # Add this page's attributes
        out += [f["attributes"] for f in page.get("features", [])]
        # Stop when the server says there is nothing more
        if not page.get("exceededTransferLimit") and len(page.get("features", [])) < 2000:
            # Leave the paging loop
            break
    # Hand back every row
    return out


# Download live postcodes whose object IDs fall in one range, paging by object ID
def download_range(lo, hi):
    # Rows collected for this range
    rows = []
    # Start just below the range
    last = lo
    # Keep fetching pages until one comes back empty
    while True:
        # Ask for the next 2,000 live postcodes in the range
        page = get(ONSPD, {"where": f"DOTERM IS NULL AND OBJECTID>{last} AND OBJECTID<={hi}",
                           # Only the fields these lookups need
                           "outFields": "OBJECTID,PCDS,LAT,LONG,LAD26CD,CTY26CD,RGN26CD,CTRY26CD,BUA24CD",
                           # Page in object ID order, 2,000 at a time, without geometry
                           "orderByFields": "OBJECTID", "resultRecordCount": "2000", "returnGeometry": "false", "f": "json"})
        # The rows on this page
        feats = [f["attributes"] for f in page.get("features", [])]
        # Stop when there are no more rows in the range
        if not feats:
            # Leave the paging loop
            break
        # Keep rows that have real coordinates (a few non-geographic postcodes have none)
        rows += [(a["PCDS"], a["LAT"], a["LONG"], a["LAD26CD"], a["CTY26CD"], a["RGN26CD"], a["CTRY26CD"], a["BUA24CD"]) for a in feats if a["LAT"] and a["LONG"] and a["LAT"] < 90]
        # Remember the last object ID for the next page
        last = feats[-1]["OBJECTID"]
    # Report the range total
    print(f"  range {lo:,}-{hi:,}: {len(rows):,} postcodes", flush=True)
    # Hand back the rows
    return rows


# Use the cache when it exists
if CACHE.exists():
    # Read the cached rows
    postcodes = [tuple(r) for r in json.load(gzip.open(CACHE, "rt", encoding="utf-8"))]
    # Say so
    print(f"Using cached postcodes ({len(postcodes):,}) from {CACHE}")
# Otherwise download them
else:
    # Find the largest object ID in the layer
    stats = get(ONSPD, {"where": "1=1", "outStatistics": json.dumps([{"statisticType": "max", "onStatisticField": "OBJECTID", "outStatisticFieldName": "maxid"}]), "f": "json"})
    # Read it from the statistics response
    max_id = stats["features"][0]["attributes"]["maxid"]
    # Split the ID range into several chunks so the pool stays busy
    edges = [round(max_id * i / (STREAMS * 4)) for i in range(STREAMS * 4 + 1)]
    # Announce the download
    print(f"Downloading live UK postcodes (max OBJECTID {max_id:,}) in {len(edges) - 1} ranges, {STREAMS} at a time...", flush=True)
    # Run the ranges a few at a time
    with ThreadPoolExecutor(max_workers=STREAMS) as pool:
        # Collect every range's rows into one list
        postcodes = [row for part in pool.map(lambda ab: download_range(*ab), zip(edges[:-1], edges[1:])) for row in part]
    # Make the cache folder
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    # Write the rows to the cache
    json.dump(postcodes, gzip.open(CACHE, "wt", encoding="utf-8"))
# Print the total
print(f"  {len(postcodes):,} live UK postcodes with coordinates")


# Turn a postcode's council code into the April 2023 code the population data uses
def council_2023(code):
    # Recode the two councils renumbered in 2026; leave every other code alone
    return NEWER_TO_2023.get(code, code)


# Postcode counts per council, used to pick each council's county and region by majority
council_parent = defaultdict(Counter)
# Postcode counts per town, used to pick each town's council by majority
town_council = defaultdict(Counter)
# Running latitude and longitude totals per town, for its centre
town_sum = defaultdict(lambda: [0.0, 0.0, 0])
# Postcodes grouped by postcode district (outward code) and by sector
by_district = defaultdict(list)
# Postcodes grouped by sector ("LA9 4")
by_sector = defaultdict(list)
# A coarse grid of postcodes for finding the nearest one to a named place
grid = defaultdict(list)
# Go through every postcode once
for pcds, lat, lon, lad, cty, rgn, ctry, bua in postcodes:
    # Use the April 2023 council code
    lad = council_2023(lad)
    # A real county code (E10...) means a two-tier area; otherwise the council is its own county or unitary
    county = cty if cty and cty.startswith("E10") else lad
    # English regions have E12 codes; elsewhere the nation stands in for the region
    region = rgn if rgn and rgn.startswith("E12") else NATION_OF[lad[0]]
    # Count this postcode towards the council's county, region and nation
    council_parent[lad][(county, region, NATION_OF[lad[0]])] += 1
    # A real built-up area code is E63... or W45...; anything else (blank or a pseudo code) means "not in a town"
    town = bua if bua and bua[:3] in ("E63", "W45") else ""
    # Count this postcode towards its town's council and centre
    if town:
        # One more postcode in this council for this town
        town_council[town][lad] += 1
        # Add to the town's coordinate totals
        s = town_sum[town]
        # Latitude total
        s[0] += lat
        # Longitude total
        s[1] += lon
        # Postcode count
        s[2] += 1
    # The outward code is everything before the space
    outward, inward = pcds.split(" ")
    # Group by district
    by_district[outward].append((lad, town))
    # Group by sector (outward code plus the first digit of the inward code, with no space as in the index's IDs)
    by_sector[outward + inward[0]].append((lad, town))
    # File the postcode in a 0.05-degree grid cell
    grid[(round(lat * 20), round(lon * 20))].append((lat, lon, lad, town))


# Pick the most common council and (if it holds at least half the postcodes) the most common town of a group
def majority(members):
    # Count councils
    lads = Counter(m[0] for m in members)
    # Count towns, including "no town"
    towns = Counter(m[1] for m in members)
    # The leading town and its share
    top_town, n = towns.most_common(1)[0]
    # Keep the town only when it is a real town and holds half or more of the postcodes
    return lads.most_common(1)[0][0], (top_town if top_town and n * 2 >= len(members) else "")


# Find the council and town of the postcode nearest to a point (within about 2 km; else council only)
def nearest(lat, lon):
    # Best distance so far, in kilometres
    best = (1e9, None)
    # Look in the point's grid cell and its neighbours
    for di in (-1, 0, 1):
        # Rows of cells
        for dj in (-1, 0, 1):
            # Postcodes in this cell
            for plat, plon, lad, town in grid.get((round(lat * 20) + di, round(lon * 20) + dj), []):
                # Equirectangular distance in km (accurate enough at this range)
                d = math.hypot((plat - lat) * 110.57, (plon - lon) * 111.32 * math.cos(math.radians(lat)))
                # Keep the closest
                if d < best[0]:
                    # Remember it
                    best = (d, (lad, town))
    # No postcode nearby at all (remote islands): no codes
    if best[1] is None:
        # Blank council and town
        return "", ""
    # Further than 2 km from any postcode: keep the council but not the town
    return (best[1][0], best[1][1]) if best[0] <= 2 else (best[1][0], "")


# Read the UK Fuel Watch place index
rows = list(csv.DictReader(gzip.open(FUEL_INDEX, "rt", encoding="utf-8")))
# Add the two codes to every place
for r in rows:
    # The part of the ID after the type letter
    key = r["PlaceID"][2:]
    # Postcode districts use their district's postcodes
    if r["PlaceID"].startswith("D:") and key in by_district:
        # Majority council and town
        r["CouncilCode"], r["TownCode"] = majority(by_district[key])
    # Postcode sectors use their sector's postcodes
    elif r["PlaceID"].startswith("S:") and key in by_sector:
        # Majority council and town
        r["CouncilCode"], r["TownCode"] = majority(by_sector[key])
    # Named places use the nearest postcode
    else:
        # Nearest postcode's council and town
        r["CouncilCode"], r["TownCode"] = nearest(float(r["Lat"]), float(r["Lon"]))
# Write the extended index
with gzip.open(OUTDIR / "place_index.csv.gz", "wt", newline="", encoding="utf-8") as f:
    # Write rows with every original column plus the two codes
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    # Header first
    w.writeheader()
    # Then every place
    w.writerows(rows)
# Report coverage
print(f"place_index: {len(rows):,} places, {sum(1 for r in rows if r['CouncilCode']):,} with a council, {sum(1 for r in rows if r['TownCode']):,} with a town")

# Read each council's land area and centre from the boundary layer
lad_rows = all_rows(LAD_BFC, "LAD26CD,LAD26NM,LAT,LONG,Shape__Area")
# Write the council lookup
with open(OUTDIR / "council_lookup.csv", "w", newline="", encoding="utf-8") as f:
    # A plain CSV writer
    w = csv.writer(f)
    # Header row
    w.writerow(["CouncilCode", "CouncilName", "CountyCode", "RegionCode", "NationCode", "Lat", "Lon", "AreaKm2"])
    # One row per council, sorted by code
    for a in sorted(lad_rows, key=lambda a: council_2023(a["LAD26CD"])):
        # The April 2023 code
        code = council_2023(a["LAD26CD"])
        # The most common county, region and nation among its postcodes
        county, region, nation = council_parent[code].most_common(1)[0][0]
        # Area: square metres on the British National Grid -> square kilometres
        w.writerow([code, a["LAD26NM"], county, region, nation, round(a["LAT"], 5), round(a["LONG"], 5), round(a["Shape__Area"] / 1e6, 2)])
# Report it
print(f"council_lookup: {len(lad_rows)} councils")

# Read each town's name and area from the built-up area layer
bua_rows = all_rows(BUA, "OBJECTID,BUA24CD,BUA24NM,areahectar")
# Write the town lookup
with open(OUTDIR / "town_lookup.csv", "w", newline="", encoding="utf-8") as f:
    # A plain CSV writer
    w = csv.writer(f)
    # Header row
    w.writerow(["TownCode", "TownName", "CouncilCode", "Lat", "Lon", "AreaKm2", "Postcodes"])
    # One row per town, sorted by code
    for a in sorted(bua_rows, key=lambda a: a["BUA24CD"]):
        # The town's postcode totals (a few tiny towns may have none)
        s = town_sum.get(a["BUA24CD"])
        # Skip towns with no postcodes; Power BI will show them without a council or dot
        if not s:
            # Report and move on
            print("  no postcodes in", a["BUA24CD"], a["BUA24NM"])
            # Next town
            continue
        # Council holding most of the town's postcodes
        lad = town_council[a["BUA24CD"]].most_common(1)[0][0]
        # Hectares -> square kilometres
        w.writerow([a["BUA24CD"], a["BUA24NM"], lad, round(s[0] / s[2], 5), round(s[1] / s[2], 5), round(a["areahectar"] / 100, 3), s[2]])
# Report it
print(f"town_lookup: {len(bua_rows)} towns")

# Write the council code map (older and newer codes -> April 2023)
with open(OUTDIR / "council_code_map.csv", "w", newline="", encoding="utf-8") as f:
    # A plain CSV writer
    w = csv.writer(f)
    # Header row
    w.writerow(["FromCode", "CouncilCode", "Why"])
    # The 2023 mergers used by the 2022-based projections
    for old, new in sorted(OLDER_TO_2023.items()):
        # One row per merged district
        w.writerow([old, new, "Merged into a new unitary council in April 2023"])
    # The 2026 renumbering
    for newer, code in sorted(NEWER_TO_2023.items()):
        # One row per renumbered council
        w.writerow([newer, code, "Renumbered after a 2026 boundary change"])
# Done
print("Done")
