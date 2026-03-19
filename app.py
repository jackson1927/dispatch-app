import streamlit as st
import pandas as pd
import re
import datetime
import os

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

def find_col(df, keywords):
    for col in df.columns:
        if any(key.lower() in str(col).lower() for key in keywords):
            return col
    return None

# --- SIDEBAR: FLEET MANAGEMENT ---
with st.sidebar:
    st.header("🚛 Fleet Management")
    st.write("Select trucks available for today's route:")
    active_trucks = {}
    for t_name, t_cap in TRUCKS_MASTER.items():
        if st.checkbox(t_name, value=True, key=f"check_{t_name}"):
            active_trucks[t_name] = t_cap
    
    st.markdown("---")
    max_stops = st.slider("Max Stops per Truck", 10, 40, 22)

st.title("🚀 Propane Route Optimizer & Map")

tab1, tab2 = st.tabs(["🎯 Route Builder", "📊 Zone Analysis"])

with tab1:
    col_input, col_zones = st.columns([1, 1])
    with col_input:
        st.subheader("1. Data Input")
        telemetry_file = st.file_uploader("Upload Master Otodata CSV", type="csv")
        manual_file = st.file_uploader("Upload Your Current Plan (Optional)", type="csv")
    
    with col_zones:
        st.subheader("2. Target Zones")
        route_day = st.selectbox("Active Day", list(DEFAULT_ZONES.keys()))
        target_cities = [c.strip().upper() for c in st.text_area("Zone Cities", DEFAULT_ZONES[route_day]).split(",") if c.strip()]

    if telemetry_file:
        try:
            df = pd.read_csv(telemetry_file, encoding='latin1')
            
            # --- IDENTIFY COLUMNS ---
            name_col = find_col(df, ["Asset", "Name", "Customer"])
            city_col = find_col(df, ["City", "Town", "Location"])
            level_col = find_col(df, ["Level", "%", "Percent"])
            ullage_col = find_col(df, ["Ullage", "Room", "Volume"])
            dte_col = find_col(df, ["DTE", "Days", "Empty"])
            lat_col = find_col(df, ["Lat"])
            lon_col = find_col(df, ["Lon", "Lng"])

            # --- CLEANING & SCORING ---
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(200)
            df['DTE_Num'] = pd.to_numeric(df[dte_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(99)
            
            # --- COMBINE MANUAL + AI ---
            final_route_df = pd.DataFrame()
            if manual_file:
                m_df = pd.read_csv(manual_file, encoding='latin1')
                m_name_col = find_col(m_df, ["Name", "Customer"])
                if m_name_col:
                    manual_names = m_df[m_name_col].unique()
                    final_route_df = df[df[name_col].isin(manual_names)].copy()
                    final_route_df['Source'] = 'Manual'

            # Fill remaining capacity
            total_cap = sum(active_trucks.values())
            current_load = final_route_df['Ullage_Num'].sum() if not final_route_df.empty else 0
            
            pool = df[~df[name_col].isin(final_route_df[name_col] if not final_route_df.empty else [])]
            pool = pool[pool['In_Zone'] | (pool['DTE_Num'] <= 2)].sort_values('DTE_Num')
            
            added = []
            for _, row in pool.iterrows():
                if current_load + row['Ullage_Num'] <= total_cap:
                    row['Source'] = 'AI Suggestion'
                    added.append(row)
                    current_load += row['Ullage_Num']
            
            if added:
                final_route_df = pd.concat([final_route_df, pd.DataFrame(added)])

            # --- MAP VIEW ---
            st.divider()
            st.subheader("🗺️ Visual Route Map")
            if lat_col and lon_col:
                map_df = final_route_df.dropna(subset=[lat_col, lon_col]).rename(columns={lat_col: 'lat', lon_col: 'lon'})
                st.map(map_df)
                st.caption("Blue dots represent your combined optimized route.")
            else:
                st.warning("No Latitude/Longitude found in file. Map disabled.")

            # --- TRUCK MANIFESTS ---
            st.subheader("3. Optimized Manifests")
            t_cols = st.columns(len(active_trucks))
            temp_df = final_route_df.copy()

            for i, (t_name, t_cap) in enumerate(active_trucks.items()):
                t_manifest = []
                t_load = 0
                for idx, row in temp_df.iterrows():
                    if t_load + row['Ullage_Num'] <= t_cap:
                        t_load += row['Ullage_Num']
                        t_manifest.append(row)
                        temp_df = temp_df.drop(idx)
                
                with t_cols[i]:
                    if t_manifest:
                        m_df = pd.DataFrame(t_manifest)
                        st.success(f"**{t_name}** ({t_load:.0f} gal)")
                        st.dataframe(m_df[[name_col, city_col, 'Source']], hide_index=True)
                        st.download_button(f"📥 {t_name} CSV", m_df.to_csv(index=False), f"{t_name}.csv")

        except Exception as e:
            st.error(f"Error: {e}")
