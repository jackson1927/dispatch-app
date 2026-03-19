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

def find_col(df, keywords):
    for col in df.columns:
        if any(key.lower() in str(col).lower() for key in keywords):
            return col
    return None

st.title("🚚 Propane Dispatch Console")

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
        df = None
        # Try-Except block fixed here
        try:
            df = pd.read_csv(telemetry_file, encoding='latin1')
            st.write("### 📄 File Preview")
            st.dataframe(df.head(3))
        except Exception as e:
            st.error(f"Error loading file: {e}")

        if df is not None:
            name_col = find_col(df, ["Asset", "Name", "Customer", "Account"])
            city_col = find_col(df, ["City", "Town", "Location", "Address"])
            level_col = find_col(df, ["Level", "%", "Percent", "Reading"])
            ullage_col = find_col(df, ["Ullage", "Room", "Volume", "Fill"])
            dte_col = find_col(df, ["DTE", "Days", "Empty"])

            st.write("### 🔍 Column Detection")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.markdown(f"Name: {'✅' if name_col else '❌'}")
            c2.markdown(f"City: {'✅' if city_col else '❌'}")
            c3.markdown(f"Level: {'✅' if level_col else '❌'}")
            c4.markdown(f"Ullage: {'✅' if ullage_col else '❌'}")
            c5.markdown(f"DTE: {'✅' if dte_col else '❌'}")

            if name_col and city_col:
                df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.upper()
                df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
                df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(200) if ullage_col else 200
                
                ai_route = df[df['In_Zone']].copy()
                st.divider()
                st.subheader("3. AI Suggested Route")
                st.dataframe(ai_route[[name_col, city_col, level_col]].head(22) if level_col else ai_route[[name_col, city_col]].head(22))
                
                # Global variables for comparison tab
                st.session_state['master_df'] = df
                st.session_state['ai_route'] = ai_route
                st.session_state['name_col'] = name_col
            else:
                st.error("Missing Name or City columns. See preview above.")

with tab2:
    st.header("⚖️ Manual vs AI Comparison")
    manual_file = st.file_uploader("Upload Your Planned Route CSV", type="csv", key="manual")
    
    if manual_file and 'master_df' in st.session_state:
        m_df = pd.read_csv(manual_file, encoding='latin1')
        m_name_col = find_col(m_df, ["Name", "Customer", "Account"])
        
        if m_name_col:
            planned_names = m_df[m_name_col].unique()
            master = st.session_state['master_df']
            match_col = st.session_state['name_col']
            manual_route = master[master[match_col].isin(planned_names)].copy()
            
            ai_hits = st.session_state['ai_route']['In_Zone'].mean() * 100
            man_hits = manual_route['In_Zone'].mean() * 100
            
            ca, cb = st.columns(2)
            ca.metric("AI Zone Efficiency", f"{ai_hits:.1f}%")
            cb.metric("Manual Zone Efficiency", f"{man_hits:.1f}%")
            
            st.write("### Efficiency Gap Analysis")
            if ai_hits > man_hits:
                st.warning(f"AI is {ai_hits - man_hits:.1f}% more efficient in staying within your target zones.")
            else:
                st.success("Your manual route is optimal!")
            
            st.dataframe(manual_route[[match_col, 'City_Clean', 'In_Zone']], hide_index=True)

with tab3:
    st.header("📊 Zone Analysis")
    if os.path.exists(LOG_FILE):
        history = pd.read_csv(LOG_FILE)
        st.bar_chart(history['City_Clean'].value_counts())
    else:
        st.info("No finalized data in memory.")
