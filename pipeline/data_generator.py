"""
Chicago Precincts VAP / CVAP Data Generator
===========================================

Builds a **precinct-level** GeoPackage for the City of Chicago with both Voting
Age Population (VAP) and Citizen Voting Age Population (CVAP) broken down into
six mutually-exclusive race/ethnicity categories:

    BVAP / HVAP / AVAP / AMINVAP / OVAP / WVAP  (and their *CVAP equivalents)

The precinct geometry and the city boundary come from the local shapefile that
ships in ``data/`` (``chicago-precincts.shp``) — they are NOT downloaded. Only the
demographic tables (which the shapefile does not carry) are pulled from the
Census API, computed at the block level, then aggregated up onto the precincts.

Pipeline:
    1. load_precincts()            read chicago-precincts.shp (voting precinct map)
    2. city_boundary()             dissolve the precincts into the city boundary
    3. download_blocks()           TIGER/Line 2020 block geometries (Chicago counties)
    4. download_pl_blocks()        PL 94-171 P1 + P3 + P4 tables per block
    5. download_acs_citizenship()  ACS 5-year B05003 citizenship rates per tract
    6. build_vap_categories()      partition block VAP into the six categories
    7. estimate_cvap_by_block()    discount each VAP category by its tract rate
    8. aggregate_blocks_to_precincts()  sum block VAP/CVAP into their precinct
    9. export_to_gpkg()            write the precinct GeoPackage (+ boundary layer)

Blocks are assigned to the precinct that contains their interior point (a
point-in-polygon test, NOT a geometric clip) so counts stay whole and additive,
and blocks outside every precinct — i.e. outside the city — drop out naturally.

Every Census download step uses lazy caching: if the cache file already exists it
is loaded from disk instead of being re-downloaded.

Sources:
    - Local shapefile: data/chicago-precincts.shp (precinct + ward geometry)
    - TIGER/Line 2020: block geometries (census.gov)
    - Census API PL 94-171 (Tables P1, P3, P4): total population + VAP at block level
    - Census API ACS 5-year (Table B05003): citizenship rates at tract level

Methodology follows VAP-CVAP.pdf: VAP is partitioned exactly into six groups,
then each group is multiplied by its ACS tract-level citizenship rate (falling
back to the statewide rate where the tract denominator is too small).
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from census import Census
from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# Resolve everything relative to the repo layout so the script works no matter
# what the current working directory is.
PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PIPELINE_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
ACS_CACHE_DIR = DATA_DIR / "acs_tracts"

# Local precinct shapefile (voting precinct map + city boundary source).
PRECINCTS_SHP = DATA_DIR / "chicago-precincts.shp"
PRECINCT_KEY = "ward_preci"                # unique per precinct, e.g. "02015"

# Cache files (lazy-loaded if present)
BLOCKS_CACHE = DATA_DIR / "chicago_blocks_raw.gpkg"
PL_CACHE = DATA_DIR / "chicago_pl_blocks_p1p3p4.parquet"
OUTPUT_PATH = DATA_DIR / "chicago_precincts_vap_cvap.gpkg"
OUTPUT_LAYER = "chicago_precincts"
BOUNDARY_LAYER = "city_boundary"


# --------------------------------------------------------------------------- #
# Census API key
# --------------------------------------------------------------------------- #
load_dotenv(".env")
API_KEY = os.getenv("CENSUS_API_KEY")
if not API_KEY:
    raise ValueError(
        "CENSUS_API_KEY not found. Add it to .env  in root"
        "(get a free key at https://api.census.gov/data/key_signup.html)."
    )

# --------------------------------------------------------------------------- #
# Geography of interest
# --------------------------------------------------------------------------- #
STATE_FIPS = "17"                          # Illinois
STATE_NAME = "Illinois"
PLACE_NAME = "Chicago"

# Chicago is almost entirely in Cook County, with a small O'Hare sliver in
# DuPage. Both counties' blocks are pulled; the precinct spatial join then keeps
# only the blocks that actually fall inside the city.
CHI_COUNTIES = ["031",   # Cook
                "043",   # DuPage
                ]

# --------------------------------------------------------------------------- #
# Census vintages
# --------------------------------------------------------------------------- #
DECENNIAL_YEAR = 2020                      # PL 94-171  -> VAP (blocks)
ACS_YEAR = 2024                            # ACS 5-year -> citizenship rates (tracts)

# --------------------------------------------------------------------------- #
# Projections
# --------------------------------------------------------------------------- #
CRS_TIGER = "EPSG:4269"                    # NAD83, what TIGER ships in
CRS_EQUAL = "EPSG:26916"                   # UTM zone 16N (meters) — IL centroids/areas
CRS_WEBMAP = "EPSG:4326"                   # lat/lon for interactive maps

# --------------------------------------------------------------------------- #
# TIGER/Line base URL
# --------------------------------------------------------------------------- #
TIGER = "https://www2.census.gov/geo/tiger/TIGER2020"

# --------------------------------------------------------------------------- #
# PL 94-171 variable inventory
# P1_001N = total population
# P3/P4 = voting-age population by race/ethnicity
# --------------------------------------------------------------------------- #
P1_ALL = ["p1_001n"]
P3_ALL = [f"p3_{i:03d}n" for i in range(1, 72)]
P4_ALL = [f"p4_{i:03d}n" for i in range(1, 72)]
RAW_VARS = P1_ALL + P3_ALL + P4_ALL        # 143 variables total

# --------------------------------------------------------------------------- #
# VAP category definitions (from VAP-CVAP.pdf)
# --------------------------------------------------------------------------- #
# Table 1 — the 32 P3 rows that include "Black" (Any-Part-Black).
BVAP_VARS = [
    "p3_004n", "p3_011n", "p3_016n", "p3_017n", "p3_018n", "p3_019n",
    "p3_027n", "p3_028n", "p3_029n", "p3_030n", "p3_037n", "p3_038n",
    "p3_039n", "p3_040n", "p3_041n", "p3_042n", "p3_048n", "p3_049n",
    "p3_050n", "p3_051n", "p3_052n", "p3_053n", "p3_058n", "p3_059n",
    "p3_060n", "p3_061n", "p3_064n", "p3_065n", "p3_066n", "p3_067n",
    "p3_069n", "p3_071n",
]
assert len(BVAP_VARS) == 32

# Table 2 — (P3 row, P4 row) pairs. HVAP = sum(P3) - sum(P4).
HVAP_PAIRS = [
    ("p3_003n", "p4_005n"), ("p3_005n", "p4_007n"), ("p3_006n", "p4_008n"),
    ("p3_007n", "p4_009n"), ("p3_008n", "p4_010n"), ("p3_012n", "p4_014n"),
    ("p3_013n", "p4_015n"), ("p3_014n", "p4_016n"), ("p3_015n", "p4_017n"),
    ("p3_020n", "p4_022n"), ("p3_021n", "p4_023n"), ("p3_022n", "p4_024n"),
    ("p3_023n", "p4_025n"), ("p3_024n", "p4_026n"), ("p3_025n", "p4_027n"),
    ("p3_031n", "p4_033n"), ("p3_032n", "p4_034n"), ("p3_033n", "p4_035n"),
    ("p3_034n", "p4_036n"), ("p3_035n", "p4_037n"), ("p3_036n", "p4_038n"),
    ("p3_043n", "p4_045n"), ("p3_044n", "p4_046n"), ("p3_045n", "p4_047n"),
    ("p3_046n", "p4_048n"), ("p3_054n", "p4_056n"), ("p3_055n", "p4_057n"),
    ("p3_056n", "p4_058n"), ("p3_057n", "p4_059n"), ("p3_062n", "p4_064n"),
    ("p3_068n", "p4_070n"),
]
assert len(HVAP_PAIRS) == 31

# Table 3 — all from P4 (Not-Hispanic universe).
AVAP_VARS = [
    "p4_008n", "p4_009n", "p4_015n", "p4_016n", "p4_022n", "p4_023n",
    "p4_025n", "p4_026n", "p4_027n", "p4_033n", "p4_034n", "p4_036n",
    "p4_037n", "p4_038n", "p4_045n", "p4_046n", "p4_047n", "p4_048n",
    "p4_056n", "p4_057n", "p4_058n", "p4_059n", "p4_064n", "p4_070n",
]                                          # 24: anything with Asian or NHPI
AMINVAP_VARS = ["p4_007n", "p4_014n", "p4_024n", "p4_035n"]   # 4: AMIN, no Asian/NHPI
OVAP_VARS = ["p4_010n", "p4_017n"]                            # 2: Other, no Asian/NHPI/AMIN
WVAP_VARS = ["p4_005n"]                                       # 1: non-Hispanic single-race White
assert (len(AVAP_VARS), len(AMINVAP_VARS), len(OVAP_VARS), len(WVAP_VARS)) == (24, 4, 2, 1)

# The six mutually-exclusive VAP categories that partition total VAP.
CATEGORIES = ["BVAP", "HVAP", "AVAP", "AMINVAP", "OVAP", "WVAP"]
CVAP_CATEGORIES = ["BCVAP", "HCVAP", "ACVAP", "AMINCVAP", "OCVAP", "WCVAP"]

# --------------------------------------------------------------------------- #
# ACS B05003 citizenship-rate definitions
# --------------------------------------------------------------------------- #
# Map each ACS race-iteration suffix to the Decennial category it discounts.
ACS_RATE_TABLES = {
    "B": "BVAP",        # Black alone
    "I": "HVAP",        # Hispanic
    "D": "AVAP",        # Asian alone
    "C": "AMINVAP",     # AMIN alone
    "H": "WVAP",        # White alone, not Hispanic  (also reused for OVAP)
}
# B05003 rows that define CVAP and VAP (identical across race iterations).
CVAP_ROWS = ["009", "011", "020", "022"]   # native + naturalized, 18+, both sexes
VAP_ROWS = ["008", "019"]                  # all 18+, both sexes

# Each VAP block column is discounted by the rate of its ACS category.
# "Other" (OVAP) is folded into White's rate per the PDF footnote.
DISCOUNT_MAP = {
    "BVAP": "BVAP",
    "HVAP": "HVAP",
    "AVAP": "AVAP",
    "AMINVAP": "AMINVAP",
    "WVAP": "WVAP",
    "OVAP": "WVAP",
}

VAP_FLOOR = 20   # minimum ACS tract VAP for the tract rate to be trusted


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _chunks(seq, n):
    """Yield successive n-sized chunks (the API allows <= 50 variables per call)."""
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def b05003_vars(suffix):
    """Return the B05003<suffix> variable names needed for CVAP and VAP."""
    rows = sorted(set(CVAP_ROWS + VAP_ROWS))
    return [f"B05003{suffix}_{r}E" for r in rows]


def get_census_client(year=DECENNIAL_YEAR):
    """Return a configured Census API client for the given vintage."""
    return Census(API_KEY, year=year)


# --------------------------------------------------------------------------- #
# 1. Precinct geometry (local shapefile)
# --------------------------------------------------------------------------- #
def load_precincts(shp_path=PRECINCTS_SHP):
    """Load the Chicago voting-precinct map from the local shapefile.

    Reads ``data/chicago-precincts.shp``, keeps the ward/precinct identifiers
    and repairs any invalid geometries so the layer is safe for the union
    (city boundary) and the block spatial join.

    Args:
        shp_path: Path to the precinct shapefile.

    Returns:
        GeoDataFrame with PRECINCT_KEY, ward, precinct, geometry (in CRS_TIGER).
    """
    shp_path = Path(shp_path)
    if not shp_path.exists():
        raise FileNotFoundError(
            f"Precinct shapefile not found at {shp_path}. Expected the "
            "chicago-precincts.* files inside the data/ directory."
        )

    print("Loading Chicago precincts from local shapefile …")
    precincts = gpd.read_file(shp_path)[[PRECINCT_KEY, "ward", "precinct", "geometry"]].copy()

    # A couple of precinct polygons ship self-intersecting; make them valid so
    # union_all() and the point-in-polygon join behave.
    invalid = ~precincts.is_valid
    if invalid.any():
        print(f"  Repairing {int(invalid.sum())} invalid precinct geometries …")
        precincts.loc[invalid, "geometry"] = precincts.loc[invalid, "geometry"].make_valid()

    precincts = precincts.to_crs(CRS_TIGER)
    print(f"✓ Loaded {len(precincts):,} precincts across "
          f"{precincts['ward'].nunique()} wards.")
    return precincts


# --------------------------------------------------------------------------- #
# 2. City boundary (dissolve the precincts)
# --------------------------------------------------------------------------- #
def city_boundary(precincts):
    """Dissolve the precinct polygons into a single city-boundary polygon.

    Args:
        precincts: GeoDataFrame of precincts (from load_precincts).

    Returns:
        Single-row GeoDataFrame (city polygon) in CRS_EQUAL.
    """
    geom = precincts.to_crs(CRS_EQUAL).geometry
    poly = geom.union_all() if hasattr(geom, "union_all") else geom.unary_union
    boundary = gpd.GeoDataFrame({"name": [PLACE_NAME]}, geometry=[poly], crs=CRS_EQUAL)
    print(f"✓ Built {PLACE_NAME} boundary from the union of "
          f"{len(precincts):,} precincts.")
    return boundary


# --------------------------------------------------------------------------- #
# 3. Block geometries
# --------------------------------------------------------------------------- #
def download_blocks(cache_path=BLOCKS_CACHE):
    """Download (or load from cache) TIGER block geometries for the Chicago counties.

    Downloads the statewide 2020 block layer, filters to the Chicago-area
    counties, re-indexes on the 15-digit GEOID and keeps a slim set of columns.

    Args:
        cache_path: GeoPackage path used for lazy caching.

    Returns:
        GeoDataFrame indexed by GEOID with COUNTYFP20, TRACTCE20, ALAND20, geometry.
    """
    cache_path = Path(cache_path)
    if cache_path.exists():
        print("Loading blocks from cache …")
        block_gdf = gpd.read_file(cache_path)
        if "GEOID" in block_gdf.columns:
            block_gdf = block_gdf.set_index("GEOID")
        return block_gdf

    blocks_url = f"{TIGER}/TABBLOCK20/tl_2020_{STATE_FIPS}_tabblock20.zip"
    print("Downloading statewide blocks (this is the big one) …")
    state_blocks = gpd.read_file(blocks_url)

    block_gdf = state_blocks[state_blocks["COUNTYFP20"].isin(CHI_COUNTIES)].copy()
    block_gdf = block_gdf.rename(columns={"GEOID20": "GEOID"}).set_index("GEOID")
    block_gdf = block_gdf[["COUNTYFP20", "TRACTCE20", "ALAND20", "geometry"]]

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    block_gdf.to_file(cache_path, driver="GPKG")
    print(f"✓ Saved {len(block_gdf):,} blocks to {cache_path}")
    return block_gdf


# --------------------------------------------------------------------------- #
# 4. PL 94-171 block data (P3 + P4)
# --------------------------------------------------------------------------- #
def fetch_pl_blocks(client, variables, state_fips, counties, chunk_size=49):
    """Download PL block data for `variables` across several counties.

    The API caps each request at 50 variables, so variables are fetched in
    chunks and merged on the geography keys.

    Returns:
        DataFrame indexed by the 15-digit block GEOID with float value columns.
    """
    geo_keys = ["state", "county", "tract", "block"]
    county_frames = []
    for cty in counties:
        chunk_frames = []
        for chunk in _chunks(variables, chunk_size):
            raw = client.pl.get(
                [v.upper() for v in chunk],
                geo={"for": "block:*", "in": f"state:{state_fips} county:{cty}"},
            )
            chunk_frames.append(pd.DataFrame(raw))
        df = chunk_frames[0]
        for extra in chunk_frames[1:]:
            df = df.merge(extra, on=geo_keys)
        county_frames.append(df)

    out = pd.concat(county_frames, ignore_index=True)
    out.columns = [c.lower() for c in out.columns]
    out["GEOID"] = out["state"] + out["county"] + out["tract"] + out["block"]
    value_cols = [v.lower() for v in variables]
    out[value_cols] = out[value_cols].astype(float)
    return out.set_index("GEOID")[value_cols]


def download_pl_blocks(client=None, cache_path=PL_CACHE):
    """Download (or load from cache) the P1/P3/P4 PL table for every Chicago-county block.

    Args:
        client: Census client (defaults to a fresh PL-vintage client).
        cache_path: Parquet path used for lazy caching.

    Returns:
        DataFrame indexed by block GEOID with the 143 P1/P3/P4 variables.
    """
    cache_path = Path(cache_path)
    if cache_path.exists():
        print("Loading P1/P3/P4 data from cache …")
        return pd.read_parquet(cache_path)

    if client is None:
        client = get_census_client(DECENNIAL_YEAR)
    print("Downloading P1/P3/P4 data from Census API (this takes ~2 minutes) …")
    pl_blocks = fetch_pl_blocks(client, RAW_VARS, STATE_FIPS, CHI_COUNTIES)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pl_blocks.to_parquet(cache_path)
    print(f"✓ Saved {len(pl_blocks):,} blocks to {cache_path}")
    return pl_blocks


# --------------------------------------------------------------------------- #
# 5. ACS citizenship rates (tract level)
# --------------------------------------------------------------------------- #
def citizenship_rates(client, suffix, geo, year):
    """Return CVAP and VAP (ACS) for one B05003 race iteration.

    Args:
        client: Census client.
        suffix: B05003 race-iteration suffix (e.g. "B", "H", "I", …).
        geo: Census API geography dict.
        year: ACS 5-year vintage.

    Returns:
        DataFrame with acs_cvap and acs_vap; indexed by 11-digit tract GEOID
        when the geography is tract-level.
    """
    raw = client.acs5.get(b05003_vars(suffix), geo=geo, year=year)
    df = pd.DataFrame(raw)
    val_cols = b05003_vars(suffix)
    df[val_cols] = df[val_cols].astype(float)
    cvap = df[[f"B05003{suffix}_{r}E" for r in CVAP_ROWS]].sum(axis=1)
    vap = df[[f"B05003{suffix}_{r}E" for r in VAP_ROWS]].sum(axis=1)
    out = pd.DataFrame({"acs_cvap": cvap, "acs_vap": vap})
    if "tract" in df.columns:
        out["GEOID"] = df["state"] + df["county"] + df["tract"]
        out = out.set_index("GEOID")
    return out


def download_acs_citizenship(client=None, cache_dir=ACS_CACHE_DIR):
    """Download (or load from cache) ACS B05003 citizenship rates.

    For every race iteration in ACS_RATE_TABLES this fetches tract-level CVAP/VAP
    for all of Illinois (cached per category) plus the statewide totals used as a
    fallback when a tract denominator is too small.

    Args:
        client: Census client (defaults to a fresh ACS-vintage client).
        cache_dir: Directory for per-category tract parquet caches.

    Returns:
        (acs_tract, acs_state) where
            acs_tract: dict category -> tract DataFrame (acs_cvap, acs_vap)
            acs_state: dict category -> (statewide_cvap, statewide_vap)
    """
    if client is None:
        client = get_census_client(DECENNIAL_YEAR)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    acs_tract = {}   # category -> tract DataFrame
    acs_state = {}   # category -> (cvap, vap) statewide totals for the fallback

    for suffix, category in ACS_RATE_TABLES.items():
        cache_file = cache_dir / f"acs_{category}_tracts.parquet"

        if cache_file.exists():
            print(f"Loading {category} tract data from cache …")
            acs_tract[category] = pd.read_parquet(cache_file)
        else:
            tract_geo = {"for": "tract:*", "in": f"state:{STATE_FIPS}"}
            acs_tract[category] = citizenship_rates(client, suffix, tract_geo, ACS_YEAR)
            acs_tract[category].to_parquet(cache_file)
            print(f"✓ Saved {category} tracts to {cache_file}")

        # Statewide totals (tiny request, fetched fresh as the fallback rate).
        st = citizenship_rates(client, suffix, {"for": f"state:{STATE_FIPS}"}, ACS_YEAR)
        acs_state[category] = (float(st["acs_cvap"].iloc[0]), float(st["acs_vap"].iloc[0]))

        rate = acs_state[category][0] / acs_state[category][1]
        print(f"{category:8s} (B05003{suffix})  statewide rate = {rate:.3f}")

    return acs_tract, acs_state


# --------------------------------------------------------------------------- #
# 6. Partition VAP into the six categories
# --------------------------------------------------------------------------- #
def build_vap_categories(vap_raw):
    """Partition total VAP into the six mutually-exclusive categories.

    Builds BVAP, HVAP, AVAP, AMINVAP, OVAP, WVAP from the raw P3/P4 variables
    and verifies that they sum exactly to total VAP (P3_001N).

    Args:
        vap_raw: DataFrame of raw P3/P4 variables indexed by block GEOID.

    Returns:
        DataFrame indexed by GEOID with total_pop_20, VAP and the six categories.
    """
    vap = pd.DataFrame(index=vap_raw.index)
    vap["total_pop_20"] = vap_raw["p1_001n"]        # total population, all ages (P1_001N)
    vap["VAP"] = vap_raw["p3_001n"]                 # voting-age population, 18+ (P3_001N)

    # BVAP — sum of the 32 any-part-Black rows.
    vap["BVAP"] = vap_raw[BVAP_VARS].sum(axis=1)

    # HVAP — sum(P3) - sum(P4) over the 31 pairs.
    p3_side = [p3 for p3, _ in HVAP_PAIRS]
    p4_side = [p4 for _, p4 in HVAP_PAIRS]
    vap["HVAP"] = vap_raw[p3_side].sum(axis=1).values - vap_raw[p4_side].sum(axis=1).values

    # Remaining Not-Hispanic categories, all from P4.
    vap["AVAP"] = vap_raw[AVAP_VARS].sum(axis=1)
    vap["AMINVAP"] = vap_raw[AMINVAP_VARS].sum(axis=1)
    vap["OVAP"] = vap_raw[OVAP_VARS].sum(axis=1)
    vap["WVAP"] = vap_raw[WVAP_VARS].sum(axis=1)

    # Sanity check: the six categories must partition VAP exactly.
    recomputed = vap[CATEGORIES].sum(axis=1)
    max_err = (recomputed - vap["VAP"]).abs().max()
    assert max_err < 1e-6, (
        f"Categories do NOT partition VAP (max error {max_err}) — "
        "check the variable tables!"
    )

    # Sanity check: VAP should never exceed total population.
    bad_vap = vap["VAP"] > vap["total_pop_20"]
    assert not bad_vap.any(), (
        f"Found {bad_vap.sum()} blocks where VAP > total population. "
        "Check P1/P3 variables or Census data merge."
    )

    print(f"Blocks with total population = 0: {(vap['total_pop_20'] == 0).sum():,}")
    print(f"Blocks with VAP = 0: {(vap['VAP'] == 0).sum():,}")
    print(
        "Blocks with total population > 0 but VAP = 0: "
        f"{((vap['total_pop_20'] > 0) & (vap['VAP'] == 0)).sum():,}"
    )

    print(f" VAP partitioned into {len(CATEGORIES)} categories for "
          f"{len(vap):,} blocks (max partition error {max_err:.2e}).")
    return vap


# --------------------------------------------------------------------------- #
# 7. Per-block citizenship rate lookup
# --------------------------------------------------------------------------- #
def build_cvap_categories(category, block_index, acs_tract, acs_state):
    """Per-block citizenship rate for one category, looked up by tract.

    Uses the tract rate when the tract ACS VAP >= VAP_FLOOR, otherwise the
    statewide rate. Blocks are joined to their tract via the 11-digit GEOID
    prefix.

    Args:
        category: ACS rate category (one of ACS_RATE_TABLES values).
        block_index: Index of block GEOIDs to produce rates for.
        acs_tract: dict category -> tract DataFrame (from download_acs_citizenship).
        acs_state: dict category -> (cvap, vap) statewide totals.

    Returns:
        Series of citizenship rates aligned to block_index.
    """
    tdf = acs_tract[category]
    state_cvap, state_vap = acs_state[category]
    state_rate = state_cvap / state_vap if state_vap else 0.0

    # Tract rate, but only where the denominator is trustworthy.
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = tdf["acs_cvap"] / tdf["acs_vap"]
    rate = rate.where(tdf["acs_vap"] >= VAP_FLOOR, other=state_rate)
    rate = rate.fillna(state_rate)

    # Map the tract rate down to each block via the 11-digit tract prefix.
    block_tract = pd.Series(block_index.str[:11], index=block_index)
    return block_tract.map(rate).fillna(state_rate)


def estimate_cvap_by_block(vap, acs_tract, acs_state):
    """Discount each VAP category by its group's tract-level citizenship rate.

    Args:
        vap: DataFrame with the six VAP categories (from build_vap_categories).
        acs_tract: dict category -> tract DataFrame.
        acs_state: dict category -> (cvap, vap) statewide totals.

    Returns:
        DataFrame indexed by GEOID with the six *CVAP columns and total CVAP.
    """
    cvap = pd.DataFrame(index=vap.index)
    for vap_col, rate_cat in DISCOUNT_MAP.items():
        rate = build_cvap_categories(rate_cat, vap.index, acs_tract, acs_state)
        cvap[vap_col.replace("VAP", "CVAP")] = vap[vap_col].values * rate.values

    cvap["CVAP"] = cvap[CVAP_CATEGORIES].sum(axis=1)
    print(f"CVAP estimated for {len(cvap):,} blocks "
          f"(total CVAP = {cvap['CVAP'].sum():,.0f}).")
    return cvap


# --------------------------------------------------------------------------- #
# 8. Aggregate block VAP/CVAP onto the precincts
# --------------------------------------------------------------------------- #
def aggregate_blocks_to_precincts(blocks, precincts):
    """Sum each block's VAP/CVAP into the precinct that contains it.

    Each block is represented by an interior point and assigned to the single
    precinct that point falls inside (a point-in-polygon test, NOT a geometric
    clip), so counts stay whole and additive. Blocks whose point lies outside
    every precinct — i.e. outside the city — drop out. The work is done in the
    equal-area CRS for correct interior points, and the precinct polygons are
    returned unchanged.

    Args:
        blocks: GeoDataFrame of county blocks carrying the VAP/CVAP attributes.
        precincts: GeoDataFrame of precincts (from load_precincts).

    Returns:
        GeoDataFrame of precincts with the summed VAP/CVAP columns, in CRS_TIGER.
    """
    value_cols = ["total_pop_20", "VAP", "CVAP"] + CATEGORIES + CVAP_CATEGORIES

    blocks_eq = blocks.to_crs(CRS_EQUAL)
    precincts_eq = precincts.to_crs(CRS_EQUAL)

    # Interior point per block so each block joins to exactly one precinct.
    points = blocks_eq[value_cols].copy()
    points = gpd.GeoDataFrame(
        points, geometry=blocks_eq.geometry.representative_point(), crs=CRS_EQUAL
    )

    joined = gpd.sjoin(
        points,
        precincts_eq[[PRECINCT_KEY, "geometry"]],
        predicate="within",
        how="inner",
    )
    # Defend against a point matching more than one polygon (overlaps at repaired
    # geometries): keep the first precinct per block.
    joined = joined[~joined.index.duplicated(keep="first")]

    assigned = len(joined)
    print(f"{assigned:,} of {len(points):,} county blocks fall inside {PLACE_NAME}.")

    sums = joined.groupby(PRECINCT_KEY)[value_cols].sum()

    out = precincts.merge(sums, on=PRECINCT_KEY, how="left")
    out[value_cols] = out[value_cols].fillna(0.0)

    print(f"  {PLACE_NAME} VAP  = {out['VAP'].sum():,.0f}")
    print(f"  {PLACE_NAME} CVAP = {out['CVAP'].sum():,.0f}")
    print(f"  Precincts with zero VAP: {(out['VAP'] == 0).sum():,}")
    return out


# --------------------------------------------------------------------------- #
# 9. Export
# --------------------------------------------------------------------------- #
def export_to_gpkg(precincts, boundary=None, output_path=OUTPUT_PATH):
    """Write the precinct-level VAP/CVAP table to a GeoPackage.

    Columns are renamed to the snake_case schema the downstream pipeline reads
    (district_generator, settings_generator, summarize_results consume
    total_vap_20 + white_vap_20, etc.). The city boundary, if supplied, is
    written as a second layer in the same GeoPackage.

    Args:
        precincts: GeoDataFrame from aggregate_blocks_to_precincts.
        boundary: Optional single-row city-boundary GeoDataFrame.
        output_path: Destination GeoPackage path.

    Returns:
        The exported precinct GeoDataFrame.
    """
    output_path = Path(output_path)
    id_cols = [PRECINCT_KEY, "ward", "precinct"]
    export_cols = (id_cols + ["total_pop_20", "VAP", "CVAP"]
                   + CATEGORIES + CVAP_CATEGORIES + ["geometry"])

    export = precincts.to_crs(CRS_TIGER)[export_cols].copy()

    # Column names match the schema the redistricting pipeline expects so this
    # precinct product is a drop-in for the geodata_path in the run configs.
    rename_dict = {
        "VAP": "total_vap_20",
        "CVAP": "total_cvap_20",
        "BVAP": "bvap_20",
        "HVAP": "hvap_20",
        "AVAP": "asian_nhpi_vap_20",
        "AMINVAP": "amin_vap_20",
        "OVAP": "other_vap_20",
        "WVAP": "white_vap_20",
        "BCVAP": "bcvap_20",
        "HCVAP": "hcvap_20",
        "ACVAP": "asian_nhpi_cvap_20",
        "AMINCVAP": "amin_cvap_20",
        "OCVAP": "other_cvap_20",
        "WCVAP": "white_cvap_20",
    }
    export = export.rename(columns=rename_dict)

    # People-of-color (non-white) VAP/CVAP, materialized so configs with
    # pop_of_interest_column = "poc_vap_20" (focal group) can read it directly.
    export["poc_vap_20"] = export["total_vap_20"] - export["white_vap_20"]
    export["poc_cvap_20"] = export["total_cvap_20"] - export["white_cvap_20"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    export.to_file(output_path, layer=OUTPUT_LAYER, driver="GPKG")

    if boundary is not None:
        boundary.to_crs(CRS_TIGER).to_file(output_path, layer=BOUNDARY_LAYER, driver="GPKG")

    n_cols = len([c for c in export.columns if c != "geometry"])
    print(f"Wrote {len(export):,} precincts x {n_cols} columns -> {output_path} "
          f"(layer '{OUTPUT_LAYER}')")
    if boundary is not None:
        print(f"Wrote the {PLACE_NAME} boundary -> layer '{BOUNDARY_LAYER}'")
    print("Columns:", [c for c in export.columns if c != "geometry"])
    return export


# --------------------------------------------------------------------------- #
# Main orchestration
# --------------------------------------------------------------------------- #
def generate_data():
    """Run the full precinct-level VAP/CVAP pipeline end to end."""
    print("=" * 60)
    print("Chicago Precincts VAP / CVAP Data Generator")
    print(f"Target: {PLACE_NAME}, {STATE_NAME}")
    print("=" * 60)

    client = get_census_client(DECENNIAL_YEAR)

    # 1-2. Geometry from the local precinct shapefile.
    precincts = load_precincts()
    boundary = city_boundary(precincts)

    # 3. Block geometry (demographic backbone).
    block_gdf = download_blocks()

    # 4-5. Census tables.
    vap_raw = download_pl_blocks(client)
    acs_tract, acs_state = download_acs_citizenship(client)

    # 6-7. Block-level demographics.
    vap = build_vap_categories(vap_raw)
    cvap = estimate_cvap_by_block(vap, acs_tract, acs_state)

    # Assemble block geometry + VAP + CVAP.
    blocks = block_gdf.join(vap).join(cvap)
    print(f"  Total VAP  (Chicago counties) = {blocks['VAP'].sum():,.0f}")
    print(f"  Total CVAP (Chicago counties) = {blocks['CVAP'].sum():,.0f}")

    # 8. Aggregate blocks onto the precincts (interior-point assignment, NOT clip).
    precincts = aggregate_blocks_to_precincts(blocks, precincts)

    # 9. Export.
    export_to_gpkg(precincts, boundary)

    print("=" * 60)
    print("✓ Done.")
    print("=" * 60)
    return precincts


def main():
    """Entry point."""
    generate_data()


if __name__ == "__main__":
    main()
