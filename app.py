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

# --- APP LAYOUT ---
st.title("🚚 Propane Dispatch Console")

tab1, tab2 = st.tabs(["🎯 Dispatcher & Comparison", "📊 Analytics"])

with tab1:
    col_input, col_zones = st.columns([1, 1])
    
    with col_input:
        st.subheader("1. Upload Otodata CSV")
        telemetry_file = st.file_uploader("Drop your Master Export here", type="csv")
    
    with col_zones:
        st.subheader("2. Select Route Day")
        route_day = st.selectbox("Day", list(DEFAULT_ZONES.keys()))
        target_cities = [c.strip().upper() for c in st.text_area("Zone Cities", DEFAULT_ZONES[route_day]).split(",") if c.strip()]

    if telemetry_file:
        # Load the file
        try:
            df = pd.read_csv(telemetry_file, encoding='latin1')
            st.info(f"✅ File '{telemetry_file.name}' loaded successfully. Analyzing columns...")
        except Exception as e:
            st.error(f"❌ Error reading file: {e}")
            df = None

        if df is not None:
            # SEARCH FOR COLUMNS
            name_col = find_col(df, ["Name", "Customer", "Account", "Asset"])
            city_col = find_col(df, ["City", "Town", "Location", "Ship To"])
            ullage_col = find_col(df, ["Ullage", "Room", "Volume", "Fill"])
            dte_col = find_col(df, ["DTE", "Days to Empty", "Estimate"])
            level_col = find_col(df, ["Level", "%", "Percent"])

            # STATUS CHECKBOARD
            st.write("### 🔍 Column Detection Status")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.markdown(f"**Name:** {'✅' if name_col else '❌'}")
            c2.markdown(f"**City:** {'✅' if city_col else '❌'}")
            c3.markdown(f"**Ullage:** {'✅' if ullage_col else '❌'}")
            c4.markdown(f"**DTE:** {'✅' if dte_col else '❌'}")
            c5.markdown(f"**Level %:** {'✅' if level_col else '❌'}")

            if not name_col or not city_col:
                st.warning("⚠️ Column Mismatch! The app can't find 'Name' or 'City'. Here are the columns I see in your file:")
                st.write(list(df.columns))
                st.stop() # Prevents the rest of the app from crashing

            # DATA CLEANING
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            
            # Numeric conversion for Ullage
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)
            
            # --- AI GENERATION ---
            # Filter for "In Zone" or "Emergency (DTE 0-1)"
            ai_pool = df[df['In_Zone'] | (df[dte_col].astype(str).str.contains('0|1', na=False))].copy()
            
            st.divider()
            st.subheader("3. AI Suggested Route")
            if not ai_pool.empty:
                st.dataframe(ai_pool[[name_col, city_col, ullage_col]].head(22), use_container_width=True)
            else:
                st.info("No tanks matched the zone or emergency criteria.")

            # --- COMPARISON SECTION ---
            st.divider()
            st.subheader("⚖️ Compare Against Manual Route")
            manual_file = st.file_uploader("Upload your Manual Route CSV (Optional)", type="csv")
            
            if manual_file:
                m_df = pd.read_csv(manual_file, encoding='latin1')
                m_name_col = find_col(m_df, ["Name", "Customer", "Account"])
                
                if m_name_col:
                    planned_names = m_df[m_name_col].unique()
                    manual_route_data = df[df[name_col].isin(planned_names)].copy()
                    
                    mc1, mc2 = st.columns(2)
                    mc1.metric("AI Route Efficiency", f"{(ai_pool['In_Zone'].mean()*100):.1f}% in zone")
                    manual_efficiency = (manual_route_data['In_Zone'].mean()*100) if not manual_route_data.empty else 0
                    mc2.metric("Manual Route Efficiency", f"{manual_efficiency:.1f}% in zone")
