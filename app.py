import streamlit as st
import pandas as pd
import re
import datetime
import os
import hashlib
import json

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# --- CONFIGURATION ---
TRUCKS_MASTER = {"Truck 225": 4160, "Truck 224": 2800, "Truck 108": 2240}
DEFAULT_ZONES = {
    "Monday": "ANNA MARIA, SARASOTA, ST PETE, HOLMES BEACH, BRADENTON BEACH, LONGBOAT KEY",
    "Tuesday": "LAKELAND, HAINES CITY, POLK CITY, DAVENPORT, WINTER HAVEN, NEW PORT RICHEY, TAMPA, HUDSON, ALVA",
    "Wednesday": "TAMPA, BRADENTON, SARASOTA, RUSKIN, PALMETTO, SUN CITY CENTER, PARRISH",
    "Thursday": "TAMPA, BRANDON, PLANT CITY, VALRICO, RIVERVIEW, SEFFNER, DOVER",
    "Friday": "ORLANDO, KISSIMMEE, LAKELAND, HAINES CITY, AUBURNDALE, CLERMONT"
}

GEOCODE_CACHE_FILE = "geocode_cache.json"

st.set_page_config(page_title="Propane Dispatch Optimizer", layout="wide")

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

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def find_col(df, keywords):
    """Find a column by keyword match. Returns (col_name, matched_keyword) or (None, None)."""
    for col in df.columns:
        for key in keywords:
            if key.lower() in str(col).lower():
                return col, key
    return None, None

def detect_columns(df):
    """Detect all expected columns and return a dict with detection details."""
    detections = {}
    checks = {
        "name":   (["Asset", "Name", "Customer"], "Customer/Asset name"),
        "city":   (["City", "Town", "Location"],  "City/Location"),
        "addr":   (["Address", "Street", "Ship To"], "Street address"),
        "level":  (["Level", "%", "Percent"],     "Tank level %"),
        "ullage": (["Ullage", "Room", "Volume"],   "Ullage / fill volume"),
        "dte":    (["DTE", "Days", "Empty"],       "Days to empty"),
    }
    for field, (keywords, label) in checks.items():
        col, matched = find_col(df, keywords)
        detections[field] = {
            "col": col,
            "label": label,
            "matched": matched,
            "found": col is not None,
        }
    return detections

def parse_numeric(series):
    return pd.to_numeric(
        series.astype(str).str.replace(r"[^\d.]", "", regex=True),
        errors="coerce"
    )

def balance_trucks(route_df, active_trucks, max_stops, name_col):
    """
    Assign stops to trucks in a balanced way:
    - Sort stops by DTE ascending (most urgent first)
    - Round-robin fill trucks respecting capacity and max_stops
    - Tracks unassigned stops and returns overflow info
    """
    trucks = {
        name: {"cap": cap, "load": 0, "stops": [], "count": 0}
        for name, cap in active_trucks.items()
    }
    assigned_indices = set()
    # Sort by urgency
    sorted_df = route_df.sort_values("DTE_Num").reset_index(drop=True)

    for _, row in sorted_df.iterrows():
        ullage = row["Ullage_Num"]
        # Find best truck: least full by percentage that still fits
        best_truck = None
        best_pct = 1.1  # higher than max
        for t_name, t_info in trucks.items():
            if t_info["count"] >= max_stops:
                continue
            remaining_cap = t_info["cap"] - t_info["load"]
            if ullage <= remaining_cap:
                fill_pct = t_info["load"] / t_info["cap"]
                if fill_pct < best_pct:
                    best_pct = fill_pct
                    best_truck = t_name
        if best_truck:
            r = row.copy()
            r["Assigned_Truck"] = best_truck
            trucks[best_truck]["stops"].append(r)
            trucks[best_truck]["load"] += ullage
            trucks[best_truck]["count"] += 1
            assigned_indices.add(row.name if hasattr(row, "name") else id(row))

    overflow = sorted_df[~sorted_df.index.isin(
        [i for t in trucks.values() for i in range(len(t["stops"]))]
    )]

    # Rebuild overflow: stops not assigned
    assigned_names = set()
    for t_info in trucks.values():
        for r in t_info["stops"]:
            assigned_names.add(r[name_col])

    overflow_df = sorted_df[~sorted_df[name_col].isin(assigned_names)]

    return trucks, overflow_df

def geocode_addresses(df, addr_col, city_col, cache):
    """Geocode with caching. Returns updated df and cache."""
    geolocator = Nominatim(user_agent="propane_dispatch_v2")
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

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🚛 Fleet Management")
    active_trucks = {}
    for t_name, t_cap in TRUCKS_MASTER.items():
        if st.checkbox(t_name, value=True):
            active_trucks[t_name] = t_cap

    st.markdown("---")
    max_stops = st.slider("Max Stops per Truck", 10, 45, 22)
    dte_urgent = st.slider("🚨 Urgent DTE Threshold (days)", 1, 10, 2,
                           help="Stops with DTE at or below this are always included regardless of zone.")
    enable_geocoding = st.toggle("🛰️ Enable Address Lookup", value=True)

    st.markdown("---")
    st.caption("Truck capacities (gal):")
    for t, c in TRUCKS_MASTER.items():
        st.caption(f"• {t}: {c:,}")

# ─── TITLE ────────────────────────────────────────────────────────────────────

st.title("🚀 Propane Route Optimizer")

# ─── INPUTS ───────────────────────────────────────────────────────────────────

col_in, col_zn = st.columns(2)
with col_in:
    st.subheader("1. Data Input")
    telemetry_file = st.file_uploader("Upload Master Otodata CSV", type="csv")
    manual_file = st.file_uploader("Upload Manual Plan (Optional)", type="csv")

with col_zn:
    st.subheader("2. Target Zones")
    route_day = st.selectbox("Select Day", list(DEFAULT_ZONES.keys()))
    target_cities = [
        c.strip().upper()
        for c in st.text_area("Zone Cities", DEFAULT_ZONES[route_day]).split(",")
        if c.strip()
    ]

# ─── MAIN PROCESSING ──────────────────────────────────────────────────────────

if telemetry_file:
    try:
        df = pd.read_csv(telemetry_file, encoding="latin1")
        detections = detect_columns(df)

        # ── Column Detection Report ──
        with st.expander("🔍 Column Detection Report", expanded=False):
            det_rows = []
            for field, info in detections.items():
                det_rows.append({
                    "Field": info["label"],
                    "Detected Column": info["col"] if info["col"] else "❌ NOT FOUND",
                    "Matched Keyword": info["matched"] if info["matched"] else "—",
                    "Status": "✅" if info["found"] else "⚠️ Missing"
                })
            st.dataframe(pd.DataFrame(det_rows), hide_index=True, use_container_width=True)

        name_col  = detections["name"]["col"]
        city_col  = detections["city"]["col"]
        addr_col  = detections["addr"]["col"]
        level_col = detections["level"]["col"]
        ullage_col = detections["ullage"]["col"]
        dte_col   = detections["dte"]["col"]

        missing_critical = [
            detections[f]["label"] for f in ["name", "city"]
            if not detections[f]["found"]
        ]
        if missing_critical:
            st.error(f"❌ Cannot proceed — missing critical columns: {', '.join(missing_critical)}")
            st.stop()

        # Warn about non-critical missing columns
        missing_warn = [
            detections[f]["label"] for f in ["addr", "level", "ullage", "dte"]
            if not detections[f]["found"]
        ]
        if missing_warn:
            st.warning(f"⚠️ Could not detect: {', '.join(missing_warn)}. Defaults will be used.")

        # ── Data Cleaning ──
        df["City_Clean"] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
        df["In_Zone"] = df["City_Clean"].apply(lambda x: any(t in x for t in target_cities))
        df["Ullage_Num"] = parse_numeric(df[ullage_col]) if ullage_col else pd.Series(200, index=df.index)
        df["Ullage_Num"] = df["Ullage_Num"].fillna(200)
        df["DTE_Num"] = parse_numeric(df[dte_col]) if dte_col else pd.Series(99, index=df.index)
        df["DTE_Num"] = df["DTE_Num"].fillna(99)
        df["Level_Disp"] = df[level_col].fillna("N/A") if level_col else "N/A"

        # ── Manual Overrides ──
        final_route_df = pd.DataFrame()
        if manual_file:
            m_df = pd.read_csv(manual_file, encoding="latin1")
            m_name_col, _ = find_col(m_df, ["Name", "Customer"])
            if m_name_col:
                manual_names_raw = m_df[m_name_col].dropna().str.strip().str.upper().unique()
                df["Name_Clean"] = df[name_col].astype(str).str.strip().str.upper()
                matched = df[df["Name_Clean"].isin(manual_names_raw)].copy()
                unmatched = [
                    n for n in manual_names_raw
                    if n not in df["Name_Clean"].values
                ]
                if unmatched:
                    st.warning(
                        f"⚠️ {len(unmatched)} manual entries not found in telemetry data: "
                        f"{', '.join(unmatched[:5])}{'...' if len(unmatched) > 5 else ''}"
                    )
                if not matched.empty:
                    matched["Source"] = "Manual"
                    final_route_df = matched

        # ── AI Pool Filling ──
        total_cap = sum(active_trucks.values())
        current_load = final_route_df["Ullage_Num"].sum() if not final_route_df.empty else 0

        manual_ids = final_route_df[name_col].tolist() if not final_route_df.empty else []
        pool = df[~df[name_col].isin(manual_ids)].copy()
        pool = pool[pool["In_Zone"] | (pool["DTE_Num"] <= dte_urgent)].sort_values("DTE_Num")

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
            overflow_gal = overflow_pool["Ullage_Num"].sum()
            st.warning(
                f"⚠️ **{len(overflow_pool)} stops dropped** — capacity exceeded. "
                f"~{overflow_gal:,.0f} gal unscheduled. "
                f"Consider enabling more trucks or reducing stop ullage thresholds."
            )
            with st.expander("📋 View Overflow Stops"):
                cols_show = [c for c in [name_col, city_col, "DTE_Num", "Ullage_Num"] if c in overflow_pool.columns]
                st.dataframe(overflow_pool[cols_show].reset_index(drop=True), hide_index=True, use_container_width=True)

        if final_route_df.empty:
            st.info("No stops matched the current zone and urgency settings.")
            st.stop()

        # ── Geocoding ──
        if enable_geocoding and addr_col and city_col:
            st.divider()
            geocode_cache = load_geocode_cache()
            with st.spinner("🛰️ Geocoding addresses (cached results are instant)..."):
                final_route_df, geocode_cache = geocode_addresses(
                    final_route_df, addr_col, city_col, geocode_cache
                )
            save_geocode_cache(geocode_cache)
            mapped = final_route_df.dropna(subset=["lat", "lon"])
            if not mapped.empty:
                st.map(mapped)
            else:
                st.warning("⚠️ No addresses could be geocoded.")

        # ── Balanced Truck Assignment ──
        st.divider()
        st.subheader("3. Truck Assignments")

        trucks, overflow_df = balance_trucks(final_route_df, active_trucks, max_stops, name_col)

        if not overflow_df.empty:
            st.error(
                f"🚨 **{len(overflow_df)} stops could not be assigned** after balancing. "
                f"Increase max stops or activate more trucks."
            )

        t_cols = st.columns(len(active_trucks)) if active_trucks else st.columns(1)
        all_final = []

        for i, (t_name, t_info) in enumerate(trucks.items()):
            with t_cols[i]:
                if t_info["stops"]:
                    t_df = pd.DataFrame(t_info["stops"])
                    load = t_info["load"]
                    cap = t_info["cap"]
                    pct = load / cap * 100

                    st.success(f"**{t_name}**")
                    st.metric("Load", f"{load:,.0f} / {cap:,} gal", f"{pct:.1f}% full")
                    st.metric("Stops", t_info["count"])

                    display_cols = [c for c in [name_col, city_col, "DTE_Num", "Level_Disp", "Source"] if c in t_df.columns]
                    st.dataframe(t_df[display_cols].reset_index(drop=True), hide_index=True, use_container_width=True)
                    st.download_button(
                        f"📥 {t_name} CSV",
                        t_df.to_csv(index=False),
                        f"{t_name}_{route_day}.csv",
                        key=f"dl_{t_name}"
                    )
                    all_final.extend(t_info["stops"])
                else:
                    with t_cols[i]:
                        st.info(f"{t_name}: No stops assigned")

        # ── Summary Stats ──
        if all_final:
            st.divider()
            st.subheader("4. Summary")
            summary_rows = []
            for t_name, t_info in trucks.items():
                summary_rows.append({
                    "Truck": t_name,
                    "Stops": t_info["count"],
                    "Load (gal)": f"{t_info['load']:,.0f}",
                    "Capacity (gal)": f"{active_trucks[t_name]:,}",
                    "Utilization": f"{t_info['load'] / active_trucks[t_name] * 100:.1f}%",
                })
            st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

            total_load = sum(t["load"] for t in trucks.values())
            total_cap_active = sum(active_trucks.values())
            st.info(
                f"**Fleet total:** {total_load:,.0f} / {total_cap_active:,} gal "
                f"({total_load / total_cap_active * 100:.1f}% utilization) "
                f"across {sum(t['count'] for t in trucks.values())} stops"
            )

            # ── Master Export ──
            st.divider()
            master_df = pd.DataFrame(all_final)
            master_df["Export_Time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            master_csv = master_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 DOWNLOAD MASTER DISPATCH LIST",
                master_csv,
                f"Master_{route_day}_{datetime.date.today()}.csv",
                use_container_width=True
            )

    except Exception as e:
        st.error(f"❌ Error during processing: {e}")
        st.exception(e)

else:
    st.info("👋 Upload an Otodata CSV to begin.")
