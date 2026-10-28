# UK population data (reference files)

Prebuilt lookups for the **UK Population Watch** Power BI report. Power BI reads them from `raw.githubusercontent.com` at refresh time; the population figures themselves come live from Nomis, ONS and NISRA.

| File | What it holds | Built by |
|---|---|---|
| `reference/areas.csv` | Every area the report shows: the UK, 4 nations, 9 English regions, 21 two-tier counties, 361 councils (April 2023 codes) and 7,059 towns and villages in England and Wales (December 2024 built-up areas), with parents, centre and land area (km²) | `scripts/make_areas.py` |
| `reference/place_index.csv.gz` | 72,744 searchable places (postcode districts, sectors and named places) with the council and town each sits in | `scripts/make_area_lookups.py` |
| `reference/council_lookup.csv`, `reference/town_lookup.csv` | Council → county, region, nation; town → council; centres and areas | `scripts/make_area_lookups.py` |
| `reference/council_code_map.csv` | Older (April 2021) and newer (2026) council codes → April 2023 codes | `scripts/make_area_lookups.py` |

Rebuild yearly: `python scripts/make_area_lookups.py` then `python scripts/make_areas.py`.

**Licence:** Source: Office for National Statistics licensed under the Open Government Licence v3.0. Contains OS data © Crown copyright and database right 2026. Contains Royal Mail data © Royal Mail copyright and database right 2026.

## Licence

The code is MIT licensed (see [LICENSE](LICENSE)). The data belongs to its publishers and is used under their licences, credited above.
