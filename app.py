import streamlit as st
import pandas as pd
import re
import datetime
import os
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# --- CONFIGURATION ---
LOG_FILE = "dispatch_memory_log.csv"
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
geolocator = Nominatim(user_agent="propane_dispatch_pc")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

def find_col(df, keywords):
    for col in df.columns:
        if any(key.lower() in str(col).lower() for key in keywords):
            return col
    return None

# --- SIDEBAR ---
with st.sidebar:
    st.header("🚛 Fleet Management")
    active_trucks = {t: c for t, c in TRUCKS_MASTER.items() if st.checkbox(t, value=True)}
    st.markdown("---")
    enable_geocoding = st.toggle("🛰️ Enable Address Lookup", value=True, help="Slows down upload but enables precise mapping.")

st.title("🚀 Propane Route Optimizer")

tab1, tab2 = st.tabs(["🎯 Route Builder", "📊 Zone Analysis"])

with tab1:
    col_in, col_zn = st.columns(2)
    with col_in:
        telemetry_file = st.file_uploader("Upload Master Otodata CSV", type="csv")
    with col_zn:
        route_day = st.selectbox("Day", list(DEFAULT_ZONES.keys()))
        target_cities = [c.strip().upper() for c in st.text_area("Zones", DEFAULT_ZONES[route_day]).split(",") if c.strip()]

    if telemetry_file:
        df = pd.read_csv(telemetry_file, encoding='latin1')
        
        # Identify Columns
        name_col = find_col(df, ["Asset", "Name", "Customer"])
        city_col = find_col(df, ["City", "Town", "Location"])
        addr_col = find_col(df, ["Address", "Street", "Ship To"])
        ullage_col = find_col(df, ["Ullage", "Room", "Volume"])
        lat_col, lon_col = find_col(df, ["Lat"]), find_col(df, ["Lon", "Lng"])

        if name_col and city_col:
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(200)

            # --- SELECTION LOGIC ---
            # We filter for high priority or in-zone
            optimized_pool = df[df['In_Zone']].copy().head(60) # Capping at 60 for speed

            # --- GEOCODING LOGIC ---
            if enable_geocoding and addr_col:
                st.info("🛰️ Locating customers via Street Address... please wait.")
                progress_bar = st.progress(0)
                
                lats, lons = [], []
                for i, row in optimized_pool.iterrows():
                    # Create search string: "123 Main St, Plant City, FL"
                    search_query = f"{row[addr_col]}, {row[city_col]}, FL"
                    location = geocode(search_query)
                    
                    if location:
                        lats.append(location.latitude)
                        lons.append(location.longitude)
                    else:
                        lats.append(None)
                        lons.append(None)
                    
                    progress_bar.progress((len(lats) / len(optimized_pool)))
                
                optimized_pool['lat'] = lats
                optimized_pool['lon'] = lons
                st.success("✅ Map data generated.")

            # --- MAP VIEW ---
            st.divider()
            if 'lat' in optimized_pool.columns:
                st.subheader("🗺️ Geographic Route View")
                st.map(optimized_pool.dropna(subset=['lat', 'lon']))
            
            # --- MANIFESTS ---
           # --- MANIFESTS & MASTER EXPORT ---
            st.divider()
            st.subheader("3. Final Truck Manifests")
            
            t_cols = st.columns(len(active_trucks))
            temp_df = optimized_pool.copy()
            all_assigned_data = [] # To hold data for the Master Export

            for i, (t_name, t_cap) in enumerate(active_trucks.items()):
                t_load, t_manifest = 0, []
                for idx, row in temp_df.iterrows():
                    if t_load + row['Ullage_Num'] <= t_cap:
                        t_load += row['Ullage_Num']
                        row_with_truck = row.copy()
                        row_with_truck['Assigned_Truck'] = t_name
                        t_manifest.append(row_with_truck)
                        all_assigned_data.append(row_with_truck)
                        temp_df = temp_df.drop(idx)
                
                with t_cols[i]:
                    if t_manifest:
                        m_df = pd.DataFrame(t_manifest)
                        st.success(f"**{t_name}** ({t_load:.0f} gal)")
                        # Show Name, City, and Address in the UI
                        st.dataframe(m_df[[name_col, city_col, addr_col]], hide_index=True)
                        st.download_button(f"📥 {t_name} CSV", m_df.to_csv(index=False), f"{t_name}.csv", key=f"dl_{t_name}")
                    else:
                        st.warning(f"**{t_name}** is empty.")

            # --- FINAL MASTER EXPORT ---
            if all_assigned_data:
                st.divider()
                st.subheader("4. Finalize & Export All")
                master_df = pd.DataFrame(all_assigned_data)
                
                col_ex1, col_ex2 = st.columns([2, 1])
                with col_ex1:
                    st.write(f"The combined route for **{route_day}** is ready. This file includes all trucks in one list.")
                with col_ex2:
                    csv_master = master_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Download Master Route (CSV)",
                        data=csv_master,
                        file_name=f"Master_Dispatch_{route_day}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
        else:
            st.error("Could not find Name or City columns. Check your file headers.")
