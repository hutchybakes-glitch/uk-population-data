# UK Population Watch - builds reference/areas.csv: one row for every area the report can show, at every level
# (the UK, nations, English regions, two-tier counties, councils, and towns and villages in England and Wales),
# with its parents, centre and land area. Reads the lookups written by make_area_lookups.py plus area names from Nomis.
# Licence: OGL v3. Source: Office for National Statistics; contains OS data (c) Crown copyright and database right.
# Usage:  python scripts/make_areas.py    (run after make_area_lookups.py)

# Import csv to read and write correctly quoted rows
import csv
# Import io to read the Nomis CSV reply as a file
import io
# Import path handling
from pathlib import Path
# Import grouping helpers
from collections import defaultdict
# Import requests to call Nomis
import requests

# The data repo is the folder above this script
REPO = Path(__file__).resolve().parent.parent
# The reference folder holding the lookups
REF = REPO / "reference"
# The UK's code
UK = "K02000001"
# The four nations' codes and names
NATIONS = {"E92000001": "England", "W92000004": "Wales", "S92000003": "Scotland", "N92000002": "Northern Ireland"}


# Read a CSV file from the reference folder as a list of dictionaries
def read(name):
    # Open with UTF-8 and let csv handle quoting
    with open(REF / name, encoding="utf-8") as f:
        # Hand back every row
        return list(csv.DictReader(f))


# Councils with their county, region, nation, centre and area
councils = read("council_lookup.csv")
# Towns and villages with their council, centre and area
towns = read("town_lookup.csv")
# Ask Nomis for the names of counties, regions and nations (the latest year, all ages, one row per area)
reply = requests.get("https://www.nomisweb.co.uk/api/v01/dataset/NM_2002_1.data.csv", params={"geography": "TYPE423,TYPE480,TYPE499", "date": "latest", "gender": "0", "c_age": "200", "measures": "20100", "select": "geography_code,geography_name"}, timeout=120)
# Stop if Nomis did not answer
reply.raise_for_status()
# Area code -> name
names = {r["GEOGRAPHY_CODE"]: r["GEOGRAPHY_NAME"] for r in csv.DictReader(io.StringIO(reply.text))}
# Council names come from the boundary file
names.update({c["CouncilCode"]: c["CouncilName"] for c in councils})
# Ask Nomis which towns and villages have population estimates (2024 built-up areas, latest year)
reply = requests.get("https://www.nomisweb.co.uk/api/v01/dataset/NM_2014_1.data.csv", params={"geography": "TYPE176", "date": "latest", "gender": "0", "c_age": "200", "measures": "20100", "select": "geography_code,geography_name"}, timeout=120)
# Stop if Nomis did not answer
reply.raise_for_status()
# Town code -> name, as Nomis publishes it
nomis_towns = {r["GEOGRAPHY_CODE"]: r["GEOGRAPHY_NAME"] for r in csv.DictReader(io.StringIO(reply.text))}
# Report towns with estimates that the boundary lookup could not place (no postcodes inside them)
print("  towns with estimates but no postcodes:", sorted(set(nomis_towns) - {t["TownCode"] for t in towns}))
# Keep only towns that have estimates (the boundary layer also holds cross-border pieces with none)
towns = [t for t in towns if t["TownCode"] in nomis_towns]
# Town names as Nomis publishes them
names.update(nomis_towns)
# Nation names are fixed
names.update(NATIONS)
# The UK's name
names[UK] = "United Kingdom"

# Every output row, keyed by area code
areas = {}
# Councils grouped under each parent, for centres and areas of the higher levels
members = defaultdict(list)
# Go through the councils
for c in councils:
    # A unitary council is its own county; a district sits in a two-tier county (E10...)
    two_tier = c["CountyCode"].startswith("E10")
    # The council's row
    areas[c["CouncilCode"]] = {"AreaCode": c["CouncilCode"], "Level": "Council", "LevelOrder": 5, "CouncilCode": c["CouncilCode"],
                               # Its county (blank for a unitary), region and nation
                               "CountyCode": c["CountyCode"] if two_tier else "", "RegionCode": c["RegionCode"], "NationCode": c["NationCode"],
                               # Unitary councils also count as "counties and unitary authorities"
                               "IsCountyOrUnitary": "false" if two_tier else "true",
                               # Centre and land area
                               "Lat": c["Lat"], "Lon": c["Lon"], "AreaKm2": c["AreaKm2"]}
    # File the council under every level above it, once each (outside England the "region" is the nation itself)
    for parent in dict.fromkeys(([c["CountyCode"]] if two_tier else []) + [c["RegionCode"], c["NationCode"], UK]):
        # One entry per council under that parent
        members[parent].append(c)

# Build the counties, regions, nations and the UK from their councils
for code, group in members.items():
    # What kind of area this is
    level, order = ("UK", 1) if code == UK else ("Nation", 2) if code in NATIONS else ("Region", 3) if code.startswith("E12") else ("County", 4)
    # Wales, Scotland and Northern Ireland have no regions; their councils' "region" is the nation, which is already a nation row
    if code in areas:
        # Skip it
        continue
    # Total land area of the councils
    km2 = sum(float(c["AreaKm2"]) for c in group)
    # Area-weighted centre of the councils (good enough to place a label)
    lat = sum(float(c["Lat"]) * float(c["AreaKm2"]) for c in group) / km2
    # Same for longitude
    lon = sum(float(c["Lon"]) * float(c["AreaKm2"]) for c in group) / km2
    # The first council's parents serve the whole group (counties sit inside one region)
    first = group[0]
    # The row
    areas[code] = {"AreaCode": code, "Level": level, "LevelOrder": order, "CouncilCode": "",
                   # A county's region and nation; a region's nation; a nation's own code
                   "CountyCode": "", "RegionCode": first["RegionCode"] if level == "County" else (code if level == "Region" else ""),
                   # The nation (the UK row points at itself)
                   "NationCode": UK if level == "UK" else (code if level == "Nation" else first["NationCode"]),
                   # Two-tier counties count as "counties and unitary authorities"
                   "IsCountyOrUnitary": "true" if level == "County" else "false",
                   # Centre and area
                   "Lat": round(lat, 5), "Lon": round(lon, 5), "AreaKm2": round(km2, 2)}

# Add the towns and villages
for t in towns:
    # The council the town sits in (most of its postcodes)
    c = areas.get(t["CouncilCode"])
    # Skip any town whose council is unknown
    if c is None:
        # Report and move on
        print("  town with unknown council:", t["TownCode"], t["TownName"], t["CouncilCode"])
        # Next town
        continue
    # The row
    areas[t["TownCode"]] = {"AreaCode": t["TownCode"], "Level": "Town or village", "LevelOrder": 6, "CouncilCode": t["CouncilCode"],
                            # The council's county, region and nation
                            "CountyCode": c["CountyCode"], "RegionCode": c["RegionCode"], "NationCode": c["NationCode"],
                            # Towns are not counties
                            "IsCountyOrUnitary": "false",
                            # Centre and area
                            "Lat": t["Lat"], "Lon": t["Lon"], "AreaKm2": t["AreaKm2"]}

# Add names, and the name of the area each one sits in, for plain-English sentences ("Kendal, in Westmorland and Furness")
for a in areas.values():
    # The area's own name
    a["AreaName"] = names.get(a["AreaCode"], a["AreaCode"])
    # Names of its council, county, region and nation
    for k in ("Council", "County", "Region", "Nation"):
        # Blank when there is no such parent
        a[k + "Name"] = names.get(a[k + "Code"], "") if a[k + "Code"] else ""
    # The parent to mention: a town's council; a district's county; a unitary's region; a county's region; a region's nation
    a["ParentName"] = {"Town or village": a["CouncilName"], "Council": a["CountyName"] or a["RegionName"], "County": a["RegionName"],
                       # A region sits in England; a nation in the UK
                       "Region": a["NationName"], "Nation": "United Kingdom", "UK": ""}[a["Level"]]
    # Wales, Scotland and Northern Ireland: a council's "region" is the nation, so say the nation once
    if a["ParentName"] == a["NationName"] and a["Level"] == "Council":
        # Keep it as the nation's name
        a["ParentName"] = a["NationName"]

# The output columns in order
cols = ["AreaCode", "AreaName", "Level", "LevelOrder", "ParentName", "CouncilCode", "CouncilName", "CountyCode", "CountyName",
        # Region and nation, flags and geography
        "RegionCode", "RegionName", "NationCode", "NationName", "IsCountyOrUnitary", "Lat", "Lon", "AreaKm2"]
# Write the file, ordered by level then name
with open(REF / "areas.csv", "w", newline="", encoding="utf-8") as f:
    # A dictionary writer with fixed columns
    w = csv.DictWriter(f, fieldnames=cols)
    # Header first
    w.writeheader()
    # Then every area
    w.writerows(sorted(areas.values(), key=lambda a: (a["LevelOrder"], a["AreaName"])))
# Report the counts per level
print({lvl: sum(1 for a in areas.values() if a["Level"] == lvl) for lvl in ("UK", "Nation", "Region", "County", "Council", "Town or village")})
