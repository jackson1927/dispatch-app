import streamlit as st
import pandas as pd
import datetime
import os
import hashlib
import json
import numpy as np

import requests
import base64
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from sklearn.cluster import KMeans

# --- CONFIGURATION ---
TRUCKS_MASTER = {"Truck 225": 4160, "Truck 224": 2800, "Truck 108": 2240}

DEFAULT_ZONES = {
    "Monday":    "ANNA MARIA, HOLMES BEACH, BRADENTON BEACH, LONGBOAT KEY, SARASOTA, FORT MYERS",
    "Tuesday":   "LAKELAND, HAINES CITY, PLANT CITY, POLK CITY, DAVENPORT, WINTER HAVEN, NEW PORT RICHEY, TAMPA",
    "Wednesday": "TAMPA, RIVERVIEW, BRANDON, VALRICO, RUSKIN, SUN CITY CENTER",
    "Thursday":  "TAMPA, RIVERVIEW, BRANDON, VALRICO, RUSKIN, SUN CITY CENTER",
    "Friday":    "ORLANDO, KISSIMMEE, LAKELAND, HAINES CITY, AUBURNDALE, CLERMONT"
}

# Truck-to-zone slot mapping per day.
# "Slot A" = primary truck (225), "Slot B" = secondary truck (224 or sub like 108).
# Cities listed here are used to pre-assign stops to that truck's zone before spillover.
# Days with no entry use pure geo-clustering with no lock.
TRUCK_ZONE_SLOTS = {
    "Monday": {
        "Slot A": {
            "cities": ["ANNA MARIA", "HOLMES BEACH", "BRADENTON BEACH", "LONGBOAT KEY", "SARASOTA", "FORT MYERS"],
            "truck_preference": "Truck 225",
        },
        "Slot B": {
            "cities": ["ST PETE", "ST. PETE", "SAINT PETE", "TAMPA", "KENNETH CITY", "PINELLAS PARK"],
            "truck_preference": "Truck 224",
        },
    },
    "Tuesday": {
        "Slot A": {
            "cities": ["LAKELAND", "HAINES CITY", "PLANT CITY", "POLK CITY", "DAVENPORT", "WINTER HAVEN", "AUBURNDALE"],
            "truck_preference": "Truck 225",
        },
        "Slot B": {
            "cities": ["NEW PORT RICHEY", "TAMPA", "HUDSON", "LAND O LAKES", "LUTZ", "ZEPHYRHILLS"],
            "truck_preference": "Truck 224",
        },
    },
}

# Fallback truck for Slot B when preferred truck is unavailable (e.g. 224 is down)
SLOT_B_FALLBACK = "Truck 108"
GEOCODE_CACHE_FILE = "geocode_cache.json"
ZONE_CONFIG_FILE   = "zone_config.json"
ENV_FILE           = ".env"


st.set_page_config(page_title="Propane Dispatch Optimizer", layout="wide")

# ─── ZONE CONFIG ─────────────────────────────────────────────────────────────

def load_zone_config():
    """Load saved zones from disk. Falls back to DEFAULT_ZONES if not found."""
    if os.path.exists(ZONE_CONFIG_FILE):
        try:
            with open(ZONE_CONFIG_FILE, "r") as f:
                saved = json.load(f)
            for day, cities in DEFAULT_ZONES.items():
                if day not in saved:
                    saved[day] = cities
            return saved
        except Exception:
            pass
    return dict(DEFAULT_ZONES)

def save_zone_config(zones):
    try:
        with open(ZONE_CONFIG_FILE, "w") as f:
            json.dump(zones, f, indent=2)
        return True
    except Exception:
        return False

# ─── CREDENTIALS ─────────────────────────────────────────────────────────────

def load_credentials():
    """Load Nee-Vo credentials from .env file."""
    creds = {"username": "", "password": ""}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("NEEVO_USERNAME="):
                    creds["username"] = line.split("=", 1)[1]
                elif line.startswith("NEEVO_PASSWORD="):
                    creds["password"] = line.split("=", 1)[1]
    return creds

def save_credentials(username, password):
    """Save Nee-Vo credentials to .env file."""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            lines = [l for l in f.readlines() if not l.startswith("NEEVO_")]
    lines += [f"NEEVO_USERNAME={username}\n", f"NEEVO_PASSWORD={password}\n"]
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)

OTODATA_LOGIN_URL  = "https://neevo.otodata.ca/Account/Login"
OTODATA_DATA_URL   = (
    "https://neevo.otodata.ca/odata/TankOData?"
    "$select=SerialNumber,LastLevel,Ullage,Capacity,HoursToLimit,"
    "CustomerName,Address,City,SensorTroubleStatus,LastProductTransfer,"
    "Id,DeviceId,TankId,TankName,DispatchBy,DispatchDate,Status,"
    "StatusKey,StatusPriority,ProductName,RouteName,IsAddressNullOrEmpty,"
    "IsCityNullOrEmpty,IsCustomerNameNullOrEmpty,IsAccountNumberNullOrEmpty"
)

def fetch_otodata(username, password):
    """
    Log in to neevo.otodata.ca using ASP.NET form auth, then pull tank data.
    Uses a requests.Session to carry the auth cookie automatically.
    Returns a normalized DataFrame.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # Step 1: GET login page to grab the anti-forgery token
    login_page = session.get(OTODATA_LOGIN_URL, timeout=15, verify=True)
    login_page.raise_for_status()

    # Parse __RequestVerificationToken from the form
    import re as _re2
    token_match = _re2.search(
        r'<input[^>]+name="__RequestVerificationToken"[^>]+value="([^"]+)"',
        login_page.text
    )
    token = token_match.group(1) if token_match else ""

    # Step 2: POST credentials
    payload = {
        "UserName": username,
        "Password": password,
        "__RequestVerificationToken": token,
    }
    login_resp = session.post(
        OTODATA_LOGIN_URL,
        data=payload,
        timeout=15,
        verify=True,
        allow_redirects=True,
    )

    # Check we actually got in — failed logins usually redirect back to /Account/Login
    if "Account/Login" in login_resp.url or login_resp.status_code == 401:
        raise Exception("Login failed — check your Nee-Vo username and password.")

    # Step 3: Fetch tank data with the active session cookie
    data_resp = session.get(
        OTODATA_DATA_URL,
        headers={"Accept": "application/json;odata=verbose,text/plain, */*; q=0.01",
                 "X-Requested-With": "XMLHttpRequest"},
        timeout=30,
        verify=True,
    )
    data_resp.raise_for_status()
    payload_json = data_resp.json()

    # OData wraps results in {"value": [...]}
    tanks = payload_json.get("value", payload_json) if isinstance(payload_json, dict) else payload_json

    rows = []
    for tank in tanks:
        level_pct  = tank.get("LastLevel") or 0
        capacity   = tank.get("Capacity") or 0
        ullage_raw = tank.get("Ullage")
        ullage     = ullage_raw if ullage_raw is not None else round(capacity * (1 - level_pct / 100), 1)

        rows.append({
            "Customer Name":  tank.get("CustomerName", ""),
            "City":           tank.get("City", ""),
            "Address":        tank.get("Address", ""),
            "Level (%)":      level_pct,
            "Ullage":         ullage,
            "Capacity":       capacity,
            "DTE":            tank.get("HoursToLimit"),
            "Account number": tank.get("SerialNumber", ""),
            "lat":            None,   # neevo.otodata.ca OData doesn't return coords
            "lon":            None,
            "S/N":            tank.get("SerialNumber", ""),
            "Last Fill":      tank.get("LastProductTransfer"),
            "Status":         tank.get("Status", ""),
            "Tank Name":      tank.get("TankName", ""),
        })

    if not rows:
        raise Exception("API returned 0 tanks. Check that your account has active monitors.")

    return pd.DataFrame(rows)

# ─── GEOCODE CACHE ────────────────────────────────────────────────────────────

def load_geocode_cache():
    if os.path.exists(GEOCODE_CACHE_FILE):
        try:
            with open(GEOCODE_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_geocode_cache(cache):
    try:
        with open(GEOCODE_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass

# ─── COLUMN DETECTION ────────────────────────────────────────────────────────

def find_col(df, keywords):
    for col in df.columns:
        for key in keywords:
            if key.lower() in str(col).lower():
                return col, key
    return None, None

def detect_columns(df):
    detections = {}
    checks = {
        "name":   (["Asset", "Name", "Customer"],    "Customer/Asset name"),
        "city":   (["City", "Town", "Location"],     "City/Location"),
        "addr":   (["Address", "Street", "Ship To"], "Street address"),
        "level":  (["Level", "%", "Percent"],        "Tank level %"),
        "ullage": (["Ullage", "Room", "Volume"],     "Ullage / fill volume"),
        "dte":    (["DTE", "Days", "Empty"],         "Days to empty"),
    }
    for field, (keywords, label) in checks.items():
        col, matched = find_col(df, keywords)
        detections[field] = {"col": col, "label": label, "matched": matched, "found": col is not None}
    return detections

def parse_numeric(series):
    return pd.to_numeric(
        series.astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce"
    )

import re as _re

def parse_dte(series):
    """
    Parse DTE values from Otodata. Formats seen in real data:
      - "30 hours" / "5 hours"   → convert to fractional days (min 1)
      - "4 days" / "14 days"     → extract number as days
      - "2 months" / "3 months"  → N * 30 days
      - "> 3 months"             → treated same as N months
      - None / unrecognized      → 99
    """
    def _parse(val):
        if val is None:
            return 99.0
        s = str(val).strip().lower()
        m = _re.search(r"(\d+)\s*month", s)
        if m:
            return int(m.group(1)) * 30
        m = _re.search(r"(\d+\.?\d*)\s*hour", s)
        if m:
            return max(1.0, round(float(m.group(1)) / 24, 1))
        m = _re.search(r"(\d+\.?\d*)", s)
        if m:
            return float(m.group(1))
        return 99.0
    return series.apply(_parse)

# ─── GEOCODING ───────────────────────────────────────────────────────────────

def geocode_addresses(df, addr_col, city_col, cache):
    geolocator = Nominatim(user_agent="propane_dispatch_v3")
    geocode_fn = RateLimiter(geolocator.geocode, min_delay_seconds=1)

    lats, lons, cache_hits = [], [], 0
    prog = st.progress(0)
    status = st.empty()
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows()):
        search = f"{row[addr_col]}, {row[city_col]}, FL"
        key = hashlib.md5(search.lower().encode()).hexdigest()
        if key in cache:
            lats.append(cache[key][0])
            lons.append(cache[key][1])
            cache_hits += 1
        else:
            try:
                loc = geocode_fn(search)
                lat = loc.latitude if loc else None
                lon = loc.longitude if loc else None
            except Exception:
                lat, lon = None, None
            lats.append(lat)
            lons.append(lon)
            if lat is not None:
                cache[key] = [lat, lon]
        prog.progress((i + 1) / total)
        status.caption(f"Geocoding {i+1}/{total} — {cache_hits} from cache")

    prog.empty()
    status.empty()
    df = df.copy()
    df["lat"] = lats
    df["lon"] = lons
    return df, cache

# ─── CLUSTERING ──────────────────────────────────────────────────────────────

def cluster_stops(df, n_clusters):
    """
    K-Means cluster geocoded stops into n_clusters geographic regions.
    Stops without coordinates get cluster = -1.
    """
    df = df.copy()
    df["cluster"] = -1
    routable = df.dropna(subset=["lat", "lon"])

    if routable.empty:
        return df

    actual_clusters = min(n_clusters, len(routable))
    coords = routable[["lat", "lon"]].values
    kmeans = KMeans(n_clusters=actual_clusters, n_init=10, random_state=42)
    labels = kmeans.fit_predict(coords)
    for idx, label in zip(routable.index, labels):
        df.at[idx, "cluster"] = label

    return df

# ─── NEAREST-NEIGHBOR ROUTE SORT ─────────────────────────────────────────────

def nearest_neighbor_sort(stops_df):
    """
    Sort stops using nearest-neighbor heuristic.
    Starts from the northernmost stop (highest lat).
    Unroutable stops (no lat/lon) are appended at the end.
    """
    df = stops_df.copy().reset_index(drop=True)
    routable = df.dropna(subset=["lat", "lon"]).copy()
    unroutable = df[df["lat"].isna() | df["lon"].isna()].copy()

    if routable.empty:
        df["Stop_Order"] = range(1, len(df) + 1)
        return df

    coords = routable[["lat", "lon"]].values
    n = len(coords)
    start = int(np.argmax(coords[:, 0]))  # northernmost
    visited = [False] * n
    order = [start]
    visited[start] = True

    for _ in range(n - 1):
        current = order[-1]
        best_dist = float("inf")
        best_next = -1
        for j in range(n):
            if not visited[j]:
                dist = np.sqrt(
                    (coords[current][0] - coords[j][0]) ** 2 +
                    (coords[current][1] - coords[j][1]) ** 2
                )
                if dist < best_dist:
                    best_dist = dist
                    best_next = j
        order.append(best_next)
        visited[best_next] = True

    sorted_routable = routable.iloc[order].copy()
    sorted_routable["Stop_Order"] = range(1, len(sorted_routable) + 1)
    unroutable["Stop_Order"] = range(len(sorted_routable) + 1, len(sorted_routable) + len(unroutable) + 1)

    return pd.concat([sorted_routable, unroutable]).reset_index(drop=True)

# ─── TRUCK ASSIGNMENT ────────────────────────────────────────────────────────

def resolve_slot_truck(slot_pref, active_trucks):
    """
    Return the active truck for a slot. If the preferred truck isn't active,
    fall back to SLOT_B_FALLBACK, then any available truck.
    """
    if slot_pref in active_trucks:
        return slot_pref
    if SLOT_B_FALLBACK in active_trucks:
        return SLOT_B_FALLBACK
    # Last resort: first available
    return next(iter(active_trucks), None)

def assign_trucks_by_cluster(route_df, active_trucks, max_stops, name_col, use_geo, route_day=""):
    """
    Assignment priority:
      1. If the day has TRUCK_ZONE_SLOTS: match stops to slots by city, soft-lock to that truck.
      2. Else if geo available: K-Means cluster → truck.
      3. Else: balanced greedy by DTE.
    After assignment each truck's stops are nearest-neighbor sorted.
    Overflow always spills to the least-loaded truck with room.
    """
    truck_names = list(active_trucks.keys())
    trucks = {
        name: {"cap": cap, "load": 0, "stops": [], "count": 0}
        for name, cap in active_trucks.items()
    }
    assigned_names = set()
    sorted_df = route_df.sort_values("DTE_Num").reset_index(drop=True)

    day_slots = TRUCK_ZONE_SLOTS.get(route_day, {})

    if day_slots:
        # Build city → truck map from slots
        city_to_truck = {}
        for slot_info in day_slots.values():
            t_name = resolve_slot_truck(slot_info["truck_preference"], active_trucks)
            if t_name:
                for city in slot_info["cities"]:
                    city_to_truck[city.upper()] = t_name

        # Primary pass: assign by city match
        for _, row in sorted_df.iterrows():
            city_upper = str(row.get("City_Clean", "")).upper()
            matched_truck = None
            for city_key, t_name in city_to_truck.items():
                if city_key in city_upper:
                    matched_truck = t_name
                    break
            if matched_truck:
                t_info = trucks[matched_truck]
                if t_info["count"] < max_stops and t_info["load"] + row["Ullage_Num"] <= t_info["cap"]:
                    r = row.copy()
                    r["Assigned_Truck"] = matched_truck
                    t_info["stops"].append(r)
                    t_info["load"] += row["Ullage_Num"]
                    t_info["count"] += 1
                    assigned_names.add(row[name_col])

    elif use_geo and "cluster" in route_df.columns and (route_df["cluster"] >= 0).any():
        # Geo cluster → truck mapping
        cluster_to_truck = {i: truck_names[i % len(truck_names)] for i in range(int(route_df["cluster"].max()) + 1)}
        geo_sorted = route_df.sort_values(["cluster", "DTE_Num"]).reset_index(drop=True)
        for _, row in geo_sorted.iterrows():
            cluster_id = int(row.get("cluster", -1))
            if cluster_id == -1:
                continue
            t_name = cluster_to_truck.get(cluster_id, truck_names[0])
            t_info = trucks[t_name]
            if t_info["count"] < max_stops and t_info["load"] + row["Ullage_Num"] <= t_info["cap"]:
                r = row.copy()
                r["Assigned_Truck"] = t_name
                t_info["stops"].append(r)
                t_info["load"] += row["Ullage_Num"]
                t_info["count"] += 1
                assigned_names.add(row[name_col])

    # Spill pass: anything unassigned (overflow, no city match, unroutable)
    for _, row in sorted_df.iterrows():
        if row[name_col] in assigned_names:
            continue
        ullage = row["Ullage_Num"]
        best_truck, best_pct = None, 1.1
        for t_name, t_info in trucks.items():
            if t_info["count"] >= max_stops:
                continue
            if ullage <= t_info["cap"] - t_info["load"]:
                pct = t_info["load"] / t_info["cap"]
                if pct < best_pct:
                    best_pct = pct
                    best_truck = t_name
        if best_truck:
            r = row.copy()
            r["Assigned_Truck"] = best_truck
            trucks[best_truck]["stops"].append(r)
            trucks[best_truck]["load"] += ullage
            trucks[best_truck]["count"] += 1
            assigned_names.add(row[name_col])

    overflow_df = sorted_df[~sorted_df[name_col].isin(assigned_names)]

    # Sort each truck's stops by nearest-neighbor drive order
    for t_info in trucks.values():
        if t_info["stops"]:
            t_info["stops_df"] = nearest_neighbor_sort(pd.DataFrame(t_info["stops"]))
        else:
            t_info["stops_df"] = pd.DataFrame()

    return trucks, overflow_df

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🚛 Fleet Management")
    active_trucks = {}
    for t_name, t_cap in TRUCKS_MASTER.items():
        if st.checkbox(t_name, value=True):
            active_trucks[t_name] = t_cap

    st.markdown("---")
    max_stops = st.slider("Max Stops per Truck", 10, 45, 22)
    dte_urgent = st.slider("🚨 Urgent DTE Threshold (days)", 1, 10, 2,
                           help="Stops at or below this DTE are flagged urgent.")
    level_skip = st.slider("⛽ Skip if tank level above (%)", 10, 90, 60,
                           help="If DTE is low but tank level is above this %, assume recently filled and skip.")
    enable_geocoding = st.toggle("🛰️ Enable Address Lookup + Route Optimization", value=True)

    st.markdown("---")
    st.caption("Truck capacities (gal):")
    for t, c in TRUCKS_MASTER.items():
        st.caption(f"• {t}: {c:,}")

    st.markdown("---")
    st.subheader("🔑 Nee-Vo Credentials")
    saved_creds = load_credentials()
    neevo_user = st.text_input("Username / Email", value=saved_creds["username"])
    neevo_pass = st.text_input("Password", value=saved_creds["password"], type="password")
    if st.button("💾 Save Credentials", use_container_width=True):
        save_credentials(neevo_user, neevo_pass)
        st.success("Credentials saved!")
    creds_ready = bool(neevo_user and neevo_pass)
    if creds_ready:
        st.caption("✅ Credentials loaded.")
    else:
        st.caption("⚠️ Enter credentials to enable live data fetch.")

# ─── TITLE ───────────────────────────────────────────────────────────────────

st.title("🚀 Propane Route Optimizer")
if enable_geocoding:
    st.caption("🗺️ Route optimization ON — stops clustered by region, sorted by drive order.")
else:
    st.caption("⚡ Route optimization OFF — geocoding disabled, capacity-only assignment.")

# ─── INPUTS ──────────────────────────────────────────────────────────────────

col_in, col_zn = st.columns(2)
with col_in:
    st.subheader("1. Data Input")

    live_df = None
    fetch_error = None

    if creds_ready:
        if st.button("🔄 Fetch Live Data from Otodata", use_container_width=True):
            with st.spinner("Connecting to Otodata API..."):
                try:
                    live_df = fetch_otodata(neevo_user, neevo_pass)
                    st.session_state["live_df"] = live_df
                    st.success(f"✅ Fetched {len(live_df)} tanks from Otodata.")
                except Exception as e:
                    fetch_error = str(e)
                    st.error(f"❌ API fetch failed: {fetch_error}")
    else:
        st.info("Enter Nee-Vo credentials in the sidebar to enable live fetch.")

    # Use cached live data if available from this session
    if live_df is None and "live_df" in st.session_state:
        live_df = st.session_state["live_df"]
        st.caption("📡 Using data fetched this session.")

    st.markdown("**— or —**")
    telemetry_file = st.file_uploader("Upload Otodata CSV manually", type=["csv", "xlsx"])
    manual_file = st.file_uploader("Upload Manual Plan (Optional)", type="csv")

with col_zn:
    st.subheader("2. Target Zones")
    saved_zones = load_zone_config()
    route_day = st.selectbox("Select Day", list(DEFAULT_ZONES.keys()))

    zone_text = st.text_area(
        "Zone Cities (comma-separated)",
        value=saved_zones.get(route_day, DEFAULT_ZONES[route_day]),
        help="Edit cities for this day. Use 'Save as Default' to persist, or just run without saving for a one-off week."
    )
    target_cities = [c.strip().upper() for c in zone_text.split(",") if c.strip()]

    z_col1, z_col2, z_col3 = st.columns(3)
    with z_col1:
        if st.button("💾 Save as Default", use_container_width=True, help="Saves this zone for every future run on this day"):
            saved_zones[route_day] = zone_text
            if save_zone_config(saved_zones):
                st.success(f"Saved {route_day} zone!")
            else:
                st.error("Could not save zone config.")
    with z_col2:
        if st.button("↩️ Reset to Default", use_container_width=True, help="Revert to last saved default for this day"):
            st.rerun()
    with z_col3:
        if st.button("🗑️ Clear All Zones", use_container_width=True, help="Reset ALL days back to factory defaults"):
            if os.path.exists(ZONE_CONFIG_FILE):
                os.remove(ZONE_CONFIG_FILE)
            st.success("All zones reset.")
            st.rerun()

    if zone_text != saved_zones.get(route_day, DEFAULT_ZONES[route_day]):
        st.caption("⚠️ Unsaved changes — running with this week's override only.")

# ─── MAIN PROCESSING ─────────────────────────────────────────────────────────

data_ready = live_df is not None or telemetry_file is not None

if data_ready:
    try:
        if live_df is not None:
            df = live_df.copy()
            # Live data already has lat/lon — skip geocoding for those rows
            geo_prefilled = df["lat"].notna().any()
        elif telemetry_file is not None:
            if telemetry_file.name.endswith(".xlsx"):
                df = pd.read_excel(telemetry_file)
            else:
                df = pd.read_csv(telemetry_file, encoding="latin1")
            geo_prefilled = False
        detections = detect_columns(df)

        with st.expander("🔍 Column Detection Report", expanded=False):
            det_rows = [
                {
                    "Field": info["label"],
                    "Detected Column": info["col"] or "❌ NOT FOUND",
                    "Matched Keyword": info["matched"] or "—",
                    "Status": "✅" if info["found"] else "⚠️ Missing",
                }
                for info in detections.values()
            ]
            st.dataframe(pd.DataFrame(det_rows), hide_index=True, use_container_width=True)

        name_col   = detections["name"]["col"]
        city_col   = detections["city"]["col"]
        addr_col   = detections["addr"]["col"]
        level_col  = detections["level"]["col"]
        ullage_col = detections["ullage"]["col"]
        dte_col    = detections["dte"]["col"]

        missing_critical = [detections[f]["label"] for f in ["name", "city"] if not detections[f]["found"]]
        if missing_critical:
            st.error(f"❌ Cannot proceed — missing critical columns: {', '.join(missing_critical)}")
            st.stop()

        missing_warn = [detections[f]["label"] for f in ["addr", "level", "ullage", "dte"] if not detections[f]["found"]]
        if missing_warn:
            st.warning(f"⚠️ Could not detect: {', '.join(missing_warn)}. Defaults will be used.")

        # ── Data Cleaning ──
        df["City_Clean"] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
        df["In_Zone"] = df["City_Clean"].apply(lambda x: any(t in x for t in target_cities))
        df["Ullage_Num"] = parse_numeric(df[ullage_col]).fillna(200) if ullage_col else pd.Series(200, index=df.index)
        if dte_col:
            if geo_prefilled:
                # Live API: HoursToLimit is numeric hours — convert directly to days
                df["DTE_Num"] = pd.to_numeric(df[dte_col], errors="coerce").fillna(99 * 24) / 24
            else:
                # CSV export: text strings like "5 days", "> 3 months"
                df["DTE_Num"] = parse_dte(df[dte_col])
        else:
            df["DTE_Num"] = pd.Series(99, index=df.index)
        df["Level_Disp"] = df[level_col].fillna("N/A") if level_col else "N/A"
        df["Level_Num"] = parse_numeric(df[level_col]).fillna(0) if level_col else pd.Series(0, index=df.index)

        # ── Manual Overrides ──
        final_route_df = pd.DataFrame()
        if manual_file:
            m_df = pd.read_csv(manual_file, encoding="latin1")
            m_name_col, _ = find_col(m_df, ["Name", "Customer"])
            if m_name_col:
                manual_names_raw = m_df[m_name_col].dropna().str.strip().str.upper().unique()
                df["Name_Clean"] = df[name_col].astype(str).str.strip().str.upper()
                matched = df[df["Name_Clean"].isin(manual_names_raw)].copy()
                unmatched = [n for n in manual_names_raw if n not in df["Name_Clean"].values]
                if unmatched:
                    st.warning(
                        f"⚠️ {len(unmatched)} manual entries not found in telemetry: "
                        f"{', '.join(unmatched[:5])}{'...' if len(unmatched) > 5 else ''}"
                    )
                if not matched.empty:
                    matched["Source"] = "Manual"
                    final_route_df = matched

        # ── AI Pool Filling ──
        total_cap = sum(active_trucks.values())
        current_load = final_route_df["Ullage_Num"].sum() if not final_route_df.empty else 0
        manual_ids = final_route_df[name_col].tolist() if not final_route_df.empty else []
        # A stop is "truly urgent" if DTE is low AND level is also low (not recently filled)
        pool = df[~df[name_col].isin(manual_ids)].copy()
        pool["Truly_Urgent"] = (pool["DTE_Num"] <= dte_urgent) & (pool["Level_Num"] < level_skip)
        pool = pool[pool["In_Zone"] | pool["Truly_Urgent"]].sort_values("DTE_Num")

        added = []
        for _, row in pool.iterrows():
            if current_load + row["Ullage_Num"] <= total_cap:
                r = row.copy()
                r["Source"] = "AI Suggestion"
                added.append(r)
                current_load += row["Ullage_Num"]

        if added:
            ai_df = pd.DataFrame(added)
            final_route_df = pd.concat([final_route_df, ai_df], ignore_index=True) if not final_route_df.empty else ai_df

        # ── Overflow Warning ──
        overflow_pool = pool[~pool[name_col].isin(final_route_df[name_col])]
        if not overflow_pool.empty:
            st.warning(
                f"⚠️ **{len(overflow_pool)} stops dropped** — fleet capacity exceeded. "
                f"~{overflow_pool['Ullage_Num'].sum():,.0f} gal unscheduled."
            )
            with st.expander("📋 View Capacity Overflow Stops"):
                cols_show = [c for c in [name_col, city_col, "DTE_Num", "Ullage_Num"] if c in overflow_pool.columns]
                st.dataframe(overflow_pool[cols_show].reset_index(drop=True), hide_index=True, use_container_width=True)

        if final_route_df.empty:
            st.info("No stops matched the current zone and urgency settings.")
            st.stop()

        # ── Geocoding → Clustering → Route Sort ──
        geo_available = False
        st.divider()

        if geo_prefilled:
            # Live API data already has lat/lon — only geocode rows missing coords
            missing_geo = final_route_df["lat"].isna()
            if missing_geo.any() and addr_col and city_col and enable_geocoding:
                st.info(f"🛰️ {missing_geo.sum()} stops missing coordinates — geocoding those now...")
                geocode_cache = load_geocode_cache()
                partial = final_route_df[missing_geo].copy()
                partial, geocode_cache = geocode_addresses(partial, addr_col, city_col, geocode_cache)
                save_geocode_cache(geocode_cache)
                final_route_df.loc[missing_geo, "lat"] = partial["lat"].values
                final_route_df.loc[missing_geo, "lon"] = partial["lon"].values
            else:
                st.caption("📡 Using coordinates from Otodata API — no geocoding needed.")

        elif enable_geocoding and addr_col and city_col:
            geocode_cache = load_geocode_cache()
            st.info("🛰️ Geocoding addresses — cached results load instantly.")
            final_route_df, geocode_cache = geocode_addresses(
                final_route_df, addr_col, city_col, geocode_cache
            )
            save_geocode_cache(geocode_cache)

        no_geo = final_route_df["lat"].isna().sum() if "lat" in final_route_df.columns else len(final_route_df)
        if no_geo:
            st.warning(f"⚠️ {no_geo} stop(s) have no coordinates — appended at end of their route.")

        routable_count = final_route_df["lat"].notna().sum() if "lat" in final_route_df.columns else 0
        if routable_count > 0:
            geo_available = True
            n_clusters = len(active_trucks)
            final_route_df = cluster_stops(final_route_df, n_clusters)
            map_df = final_route_df.dropna(subset=["lat", "lon"])
            if not map_df.empty:
                st.map(map_df, latitude="lat", longitude="lon")
                st.caption(f"🗺️ {routable_count} stops mapped — {n_clusters} geographic cluster(s), one per truck.")
        else:
            st.warning("⚠️ No coordinates available. Falling back to capacity-only assignment.")

        # ── Assign + Route ──
        st.divider()
        st.subheader("3. Truck Assignments")

        trucks, overflow_df = assign_trucks_by_cluster(
            final_route_df, active_trucks, max_stops, name_col, geo_available, route_day
        )

        if not overflow_df.empty:
            st.error(
                f"🚨 **{len(overflow_df)} stops unassigned** after balancing. "
                f"Increase max stops or activate more trucks."
            )
            with st.expander("📋 View Unassigned Stops"):
                cols_show = [c for c in [name_col, city_col, "DTE_Num", "Ullage_Num"] if c in overflow_df.columns]
                st.dataframe(overflow_df[cols_show].reset_index(drop=True), hide_index=True, use_container_width=True)

        t_cols = st.columns(len(active_trucks)) if active_trucks else st.columns(1)
        all_final = []

        for i, (t_name, t_info) in enumerate(trucks.items()):
            with t_cols[i]:
                t_df = t_info.get("stops_df", pd.DataFrame())
                if not t_df.empty:
                    load = t_info["load"]
                    cap  = t_info["cap"]
                    pct  = load / cap * 100
                    st.success(f"**{t_name}**")
                    st.metric("Load", f"{load:,.0f} / {cap:,} gal", f"{pct:.1f}% full")
                    st.metric("Stops", t_info["count"])
                    display_cols = [c for c in ["Stop_Order", name_col, city_col, "DTE_Num", "Level_Disp", "Source"] if c in t_df.columns]
                    st.dataframe(t_df[display_cols].reset_index(drop=True), hide_index=True, use_container_width=True)
                    st.download_button(
                        f"📥 {t_name} CSV",
                        t_df.to_csv(index=False),
                        f"{t_name}_{route_day}.csv",
                        key=f"dl_{t_name}"
                    )
                    all_final.extend(t_df.to_dict("records"))
                else:
                    st.info(f"{t_name}: No stops assigned")

        # ── Summary ──
        if all_final:
            st.divider()
            st.subheader("4. Summary")
            summary_rows = [
                {
                    "Truck": t_name,
                    "Stops": t_info["count"],
                    "Load (gal)": f"{t_info['load']:,.0f}",
                    "Capacity (gal)": f"{active_trucks[t_name]:,}",
                    "Utilization": f"{t_info['load'] / active_trucks[t_name] * 100:.1f}%",
                    "Route Optimized": "✅" if geo_available else "⚠️ No geo",
                }
                for t_name, t_info in trucks.items()
            ]
            st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

            total_load = sum(t["load"] for t in trucks.values())
            total_cap_active = sum(active_trucks.values())
            st.info(
                f"**Fleet total:** {total_load:,.0f} / {total_cap_active:,} gal "
                f"({total_load / total_cap_active * 100:.1f}% utilization) "
                f"across {sum(t['count'] for t in trucks.values())} stops"
            )

            st.divider()
            master_df = pd.DataFrame(all_final)
            master_df["Export_Time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            st.download_button(
                "📥 DOWNLOAD MASTER DISPATCH LIST",
                master_df.to_csv(index=False).encode("utf-8"),
                f"Master_{route_day}_{datetime.date.today()}.csv",
                use_container_width=True
            )

    except Exception as e:
        st.error(f"❌ Error during processing: {e}")
        st.exception(e)

else:
    st.info("👋 Fetch live data from Otodata or upload a CSV to begin.")
