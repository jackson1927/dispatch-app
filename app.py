import streamlit as st
import pandas as pd
import re
import datetime
import os

# --- CONFIGURATION ---
LOG_FILE = "dispatch_memory_log.csv"
TRUCKS = {"Truck 225": 4160, "Truck 224": 2800, "Truck 108": 2240}
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

st.title("🚀 Propane Route Optimizer")

tab1, tab2 = st.tabs(["🎯 Generate Optimized Route", "📊 Zone Analysis"])

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
            name_col = find_col(df, ["Asset", "Name", "Customer", "Account"])
            city_col = find_col(df, ["City", "Town", "Location", "Address"])
            level_col = find_col(df, ["Level", "%", "Percent"])
            ullage_col = find_col(df, ["Ullage", "Room", "Volume"])
            dte_col = find_col(df, ["DTE", "Days", "Empty"])

            # --- CLEANING & SCORING ---
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            
            # Numeric Conversion
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(200)
            df['DTE_Num'] = pd.to_numeric(df[dte_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(99)
            
            # Scoring: Priority 1 = Emergency, Priority 2 = In Zone & Low, Priority 3 = Just In Zone
            def get_priority(row):
                if row['DTE_Num'] <= 2: return 1
                if row['In_Zone'] and row['DTE_Num'] <= 5: return 2
                if row['In_Zone']: return 3
                return 4
            
            df['Priority'] = df.apply(get_priority, axis=1)

            # --- OPTIMIZATION ENGINE ---
            st.divider()
            st.subheader("3. Optimized Combined Route")
            
            # Start with your manual list if provided
            final_route_list = []
            if manual_file:
                m_df = pd.read_csv(manual_file, encoding='latin1')
                m_name_col = find_col(m_df, ["Name", "Customer"])
                if m_name_col:
                    manual_names = m_df[m_name_col].unique()
                    final_route_list = df[df[name_col].isin(manual_names)].copy()
                    st.write(f"✅ Imported {len(final_route_list)} stops from your manual plan.")

            # Fill the rest of the trucks with high-priority zone stops
            total_capacity = sum(TRUCKS.values())
            current_load = final_route_list['Ullage_Num'].sum() if len(final_route_list) > 0 else 0
            
            # Get available tanks sorted by priority
            available_pool = df[~df[name_col].isin(final_route_list[name_col] if len(final_route_list) > 0 else [])]
            available_pool = available_pool[available_pool['Priority'] < 4].sort_values(['Priority', 'DTE_Num'])

            # Add more stops until trucks are full
            added_stops = []
            for _, row in available_pool.iterrows():
                if current_load + row['Ullage_Num'] <= total_capacity:
                    added_stops.append(row)
                    current_load += row['Ullage_Num']
            
            if added_stops:
                ai_additions = pd.DataFrame(added_stops)
                if len(final_route_list) > 0:
                    combined_route = pd.concat([final_route_list, ai_additions])
                else:
                    combined_route = ai_additions
            else:
                combined_route = final_route_list

            # --- DISPLAY TRUCK MANIFESTS ---
            truck_cols = st.columns(len(TRUCKS))
            temp_route = combined_route.copy()
            
            for i, (t_name, t_cap) in enumerate(TRUCKS.items()):
                t_manifest = []
                t_load = 0
                # Greedily fill this specific truck
                for idx, row in temp_route.iterrows():
                    if t_load + row['Ullage_Num'] <= t_cap:
                        t_load += row['Ullage_Num']
                        t_manifest.append(row)
                        temp_route = temp_route.drop(idx)
                
                with truck_cols[i]:
                    if t_manifest:
                        m_df = pd.DataFrame(t_manifest)
                        st.success(f"**{t_name}**")
                        st.metric("Load", f"{t_load:.0f} / {t_cap} gal")
                        st.dataframe(m_df[[name_col, city_col, level_col]].sort_values(city_col), hide_index=True)
                        st.download_button(f"📥 Export {t_name}", m_df.to_csv(index=False), f"{t_name}_Optimized.csv")
                    else:
                        st.warning(f"**{t_name}** is empty.")

        except Exception as e:
            st.error(f"Error during optimization: {e}")
