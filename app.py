import streamlit as st
import pandas as pd
import re
import datetime
import os
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

st.set_page_config(page_title="Propane AI Optimizer", layout="wide")

# Initialize Geocoder
geolocator = Nominatim(user_agent="propane_dispatch_pc_v1")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

def find_col(df, keywords):
    for col in df.columns:
        if any(key.lower() in str(col).lower() for key in keywords):
            return col
    return None

# --- SIDEBAR ---
with st.sidebar:
    st.header("🚛 Fleet Management")
    active_trucks = {}
    for t_name, t_cap in TRUCKS_MASTER.items():
        if st.checkbox(t_name, value=True):
            active_trucks[t_name] = t_cap
            
    st.markdown("---")
    max_stops = st.slider("Max Stops per Truck", 10, 45, 22)
    enable_geocoding = st.toggle("🛰️ Enable Address Lookup", value=True)

st.title("🚀 Propane Route Optimizer")

# --- MAIN LOGIC ---
col_in, col_zn = st.columns(2)
with col_in:
    st.subheader("1. Data Input")
    telemetry_file = st.file_uploader("Upload Master Otodata CSV", type="csv")
    manual_file = st.file_uploader("Upload Manual Plan (Optional)", type="csv")

with col_zn:
    st.subheader("2. Target Zones")
    route_day = st.selectbox("Select Day", list(DEFAULT_ZONES.keys()))
    target_cities = [c.strip().upper() for c in st.text_area("Zone Cities", DEFAULT_ZONES[route_day]).split(",") if c.strip()]

if telemetry_file:
    try:
        df = pd.read_csv(telemetry_file, encoding='latin1')
        
        # Identify Columns
        name_col = find_col(df, ["Asset", "Name", "Customer"])
        city_col = find_col(df, ["City", "Town", "Location"])
        addr_col = find_col(df, ["Address", "Street", "Ship To"])
        level_col = find_col(df, ["Level", "%", "Percent"])
        ullage_col = find_col(df, ["Ullage", "Room", "Volume"])
        dte_col = find_col(df, ["DTE", "Days", "Empty"])

        if name_col and city_col:
            # Data Cleaning
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(200)
            df['DTE_Num'] = pd.to_numeric(df[dte_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(99)
            df['Level_Disp'] = df[level_col].fillna("N/A") if level_col else "N/A"

            # --- COMBINE MANUAL + AI ---
            final_route_df = pd.DataFrame()
            if manual_file:
                m_df = pd.read_csv(manual_file, encoding='latin1')
                m_name_col = find_col(m_df, ["Name", "Customer"])
                if m_name_col:
                    manual_names = m_df[m_name_col].unique()
                    final_route_df = df[df[name_col].isin(manual_names)].copy()
                    if not final_route_df.empty:
                        final_route_df['Source'] = 'Manual'

            # Fill Capacity
            total_cap = sum(active_trucks.values())
            current_load = final_route_df['Ullage_Num'].sum() if not final_route_df.empty else 0
            
            # Exclude manual picks from the pool
            manual_ids = final_route_df[name_col].tolist() if not final_route_df.empty else []
            pool = df[~df[name_col].isin(manual_ids)]
            pool = pool[pool['In_Zone'] | (pool['DTE_Num'] <= 2)].sort_values('DTE_Num')
            
            added = []
            for _, row in pool.iterrows():
                if current_load + row['Ullage_Num'] <= total_cap:
                    row_copy = row.copy()
                    row_copy['Source'] = 'AI Suggestion'
                    added.append(row_copy)
                    current_load += row['Ullage_Num']
            
            if added:
                if not final_route_df.empty:
                    final_route_df = pd.concat([final_route_df, pd.DataFrame(added)])
                else:
                    final_route_df = pd.DataFrame(added)

            # --- MAP VIEW ---
            if enable_geocoding and addr_col and not final_route_df.empty:
                st.divider()
                st.info("🛰️ Locating stops... this may take a moment.")
                prog = st.progress(0)
                lats, lons = [], []
                for i, (idx, row) in enumerate(final_route_df.iterrows()):
                    search = f"{row[addr_col]}, {row[city_col]}, FL"
                    loc = geocode(search)
                    lats.append(loc.latitude if loc else None)
                    lons.append(loc.longitude if loc else None)
                    prog.progress((i + 1) / len(final_route_df))
                
                final_route_df['lat'] = lats
                final_route_df['lon'] = lons
                st.map(final_route_df.dropna(subset=['lat', 'lon']))

            # --- MANIFESTS ---
            st.divider()
            st.subheader("3. Truck Assignments")
            t_cols = st.columns(len(active_trucks)) if active_trucks else st.columns(1)
            temp_df = final_route_df.copy()
            all_final = []

            for i, (t_name, t_cap) in enumerate(active_trucks.items()):
                t_load, t_list = 0, []
                for idx, row in temp_df.iterrows():
                    if (t_load + row['Ullage_Num'] <= t_cap) and (len(t_list) < max_stops):
                        t_load += row['Ullage_Num']
                        r = row.copy()
                        r['Assigned_Truck'] = t_name
                        t_list.append(r)
                        all_final.append(r)
                        temp_df = temp_df.drop(idx)
                
                with t_cols[i]:
                    if t_list:
                        m_df = pd.DataFrame(t_list)
                        st.success(f"**{t_name}** ({t_load:.0f} gal)")
                        st.dataframe(m_df[[name_col, city_col, 'Level_Disp', 'Source']], hide_index=True)
                        st.download_button(f"📥 {t_name} CSV", m_df.to_csv(index=False), f"{t_name}.csv", key=f"dl_{t_name}")

            # --- MASTER EXPORT ---
            if all_final:
                st.divider()
                master_df = pd.DataFrame(all_final)
                master_csv = master_df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 DOWNLOAD MASTER DISPATCH LIST", master_csv, f"Master_{route_day}.csv", use_container_width=True)

        else:
            st.error("❌ Could not find Name or City columns in the CSV.")
    except Exception as e:
        st.error(f"❌ Error during processing: {e}")
else:
    st.info("👋 Upload an Otodata CSV to begin.")
