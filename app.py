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

# --- IMPROVED COLUMN HUNTER ---
def find_col(df, keywords):
    for col in df.columns:
        # Check if any keyword is a SUBSET of the column name (e.g. "Asset" matches "Asset Name")
        if any(key.lower() in str(col).lower() for key in keywords):
            return col
    return None

st.title("🚚 Propane Dispatcher")

tab1, tab2, tab3 = st.tabs(["🎯 Dispatcher", "⚖️ Comparison", "📊 Analytics"])

with tab1:
    col_input, col_zones = st.columns([1, 1])
    with col_input:
        st.subheader("1. Upload Data")
        telemetry_file = st.file_uploader("Upload Otodata Export", type="csv")
    
    with col_zones:
        st.subheader("2. Select Day")
        route_day = st.selectbox("Day", list(DEFAULT_ZONES.keys()))
        target_cities = [c.strip().upper() for c in st.text_area("Cities", DEFAULT_ZONES[route_day]).split(",") if c.strip()]

    if telemetry_file:
        try:
            # Try different encodings in case Otodata used a special one
            df = pd.read_csv(telemetry_file, encoding='latin1')
            
            # SHOW PREVIEW SO WE CAN SEE HEADERS
            st.write("### 📄 File Preview (First 3 Rows)")
            st.dataframe(df.head(3))
            
            # SEARCH FOR COLUMNS (Aggressive Keywords)
            name_col = find_col(df, ["Asset", "Name", "Customer", "Account", "Site"])
            city_col = find_col(df, ["City", "Town", "Location", "Ship", "Address"])
            level_col = find_col(df, ["Level", "%", "Percent", "Reading", "Current"])
            ullage_col = find_col(df, ["Ullage", "Room", "Volume", "Fill", "Capacity"])
            dte_col = find_col(df, ["DTE", "Days", "Empty", "Forecast"])

            # DETECTION CHECKLIST
            st.write("### 🔍 Column Detection Status")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.markdown(f"**Name:** {'✅' if name_col else '❌'}")
            c2.markdown(f"**City:** {'✅' if city_col else '❌'}")
            c3.markdown(f"**Level:** {'✅' if level_col else '❌'}")
            c4.markdown(f"**Ullage:** {'✅' if ullage_col else '❌'}")
            c5.markdown(f"**DTE:** {'✅' if dte_col else '❌'}")

            if name_col and city_col:
                # CLEAN DATA
                df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
                df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
                
                # Numeric conversions
                if ullage_col:
                    df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)
                else:
                    df['Ullage_Num'] = 200 # Fallback if ullage missing

                # AI SUGGESTION
                ai_route = df[df['In_Zone']].copy()
                st.divider()
                st.subheader("3. AI Suggested Route")
                st.dataframe(ai_route[[name_col, city_col, level_col if level_col else name_col]].head(22))
                
                # STORE AI ROUTE FOR TAB 2
                st.session_state['ai_route'] = ai_route
                st.session_state['master_df'] = df
                st.session_state['name_col'] = name_col
            else:
                st.error("❌ Could not find Name or City columns. Please check the 'File Preview' above and tell me the header names!")

with tab2:
    st.header("⚖️ Manual vs AI Comparison")
    manual_file = st.file_uploader("Upload your Manual Route CSV", type="csv", key="comp")
    
    if manual_file and 'master_df' in st.session_state:
        m_df = pd.read_csv(manual_file, encoding='latin1')
        m_name_col = find_col(m_df, ["Name", "Customer", "Account"])
        
        if m_name_col:
            # Cross-ref manual list against master data
            manual_names = m_df[m_name_col].unique()
            master = st.session_state['master_df']
            match_col = st.session_state['name_col']
            
            manual_route = master[master[match_col].isin(manual_names)].copy()
            
            ca, cb = st.columns(2)
            ca.metric("AI Zone Adherence", f"{(st.session_state['ai_route']['In_Zone'].mean()*100):.1f}%")
            cb.metric("Manual Zone Adherence", f"{(manual_route['In_Zone'].mean()*100):.1f}%")
            
            st.write("### Route Breakdown")
            st.dataframe(manual_route[[match_col, 'City_Clean', 'In_Zone']])
