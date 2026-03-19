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

st.set_page_config(page_title="Propane Dispatch AI", layout="wide")

# --- HELPER: COLUMN HUNTER ---
def find_col(df, keywords):
    for col in df.columns:
        if any(key.lower() in str(col).lower() for key in keywords):
            return col
    return None

# --- MAIN APP LOGIC ---
tab1, tab2, tab3 = st.tabs(["🎯 Dispatcher", "⚖️ Route Comparison", "📊 Zone Analysis"])

with tab1:
    st.title("🚚 Smart Dispatcher")
    telemetry_file = st.file_uploader("Step 1: Upload Master Otodata CSV", type="csv")
    
    # Zone Selection
    route_day = st.selectbox("Select Active Route Day", list(DEFAULT_ZONES.keys()))
    target_cities = [c.strip().upper() for c in st.text_area("Target Cities", DEFAULT_ZONES[route_day]).split(",") if c.strip()]

    if telemetry_file:
        df = pd.read_csv(telemetry_file, encoding='latin1')
        
        # Identify Columns
        name_col = find_col(df, ["Name", "Customer", "Account"])
        city_col = find_col(df, ["City", "Town", "Location"])
        ullage_col = find_col(df, ["Ullage", "Room", "Volume"])
        dte_col = find_col(df, ["DTE", "Days to Empty"])

        if name_col and city_col:
            # AI Logic (Simplified for brevity)
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").str.upper()
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)
            
            # Create AI Route
            ai_pool = df[df['In_Zone'] | (df[dte_col].str.contains('0|1', na=False))].copy()
            ai_route = ai_pool.head(22) # Simulating a single truck cap for comparison
            
            st.success("AI Route Generated. Switch to 'Route Comparison' to compare against your manual list.")
            st.dataframe(ai_route[[name_col, city_col, ullage_col]], hide_index=True)

with tab2:
    st.header("⚖️ AI vs. Manual Comparison")
    st.info("Upload the CSV of the route you planned to run manually to see the efficiency difference.")
    
    manual_file = st.file_uploader("Step 2: Upload Your Manual Route CSV", type="csv")
    
    if telemetry_file and manual_file:
        # Load Manual Data
        m_df = pd.read_csv(manual_file, encoding='latin1')
        m_name_col = find_col(m_df, ["Name", "Customer", "Account"])
        
        if m_name_col:
            # Cross-reference Manual Names against Master Data to get City/Zone info
            planned_names = m_df[m_name_col].unique()
            manual_route_data = df[df[name_col].isin(planned_names)].copy()
            
            # CALCULATE STATS
            ai_zone_hits = ai_route['In_Zone'].sum()
            man_zone_hits = manual_route_data['In_Zone'].sum()
            
            # UI Comparison
            col_a, col_b = st.columns(2)
            
            with col_a:
                st.subheader("🤖 AI Suggested Route")
                st.metric("Total Stops", len(ai_route))
                st.metric("Zone Adherence", f"{(ai_zone_hits/len(ai_route)*100):.1f}%")
                st.write("Stops inside defined zones.")
                
            with col_b:
                st.subheader("📝 Your Manual Route")
                st.metric("Total Stops", len(manual_route_data))
                st.metric("Zone Adherence", f"{(man_zone_hits/len(manual_route_data)*100):.1f}%" if len(manual_route_data)>0 else "0%")
                st.write("Stops outside defined zones (Out-of-route mileage).")

            st.markdown("---")
            st.write("### 🌍 Geographic Scatter")
            if man_zone_hits < ai_zone_hits:
                st.warning(f"The AI found {ai_zone_hits - man_zone_hits} more stops within your target zones than the manual route. This suggests the manual route may have high 'deadhead' mileage.")
            else:
                st.success("Your manual route is highly efficient!")

with tab3:
    st.write("Historical analysis appears here after finalizing.")
