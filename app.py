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

# --- APP TABS ---
tab1, tab2 = st.tabs(["🚛 Dispatcher", "📊 Zone Analysis (Memory)"])

with tab1:
    st.title("Smart-Zone Dispatcher")
    
    # --- SIDEBAR: SETTINGS ---
    st.sidebar.header("🛠️ Route Controls")
    max_stops = st.sidebar.slider("Max Stops Per Truck", 5, 40, 22)
    
    st.sidebar.markdown("---")
    st.sidebar.header("🗺️ Zone Manager")
    current_zones = {}
    for day, cities in DEFAULT_ZONES.items():
        current_zones[day] = st.sidebar.text_area(f"{day} Cities", value=cities, height=68)

    today_name = datetime.datetime.now().strftime("%A")
    route_day = st.sidebar.selectbox("Select Active Route Day", list(current_zones.keys()), index=list(current_zones.keys()).index(today_name))
    target_cities = [c.strip().upper() for c in current_zones[route_day].split(",") if c.strip()]

    # --- HELPERS ---
    def find_column(df, possible_names):
        for col in df.columns:
            if any(name.lower() in str(col).lower() for name in possible_names): return col
        return None

    def get_days_num(val):
        val = str(val).lower()
        if any(x in val for x in ['hour', 'minute', 'now', '0']): return 0
        nums = re.findall(r'\d+', val)
        return int(nums[0]) if nums else 999

    # --- FILE UPLOAD ---
    telemetry_file = st.file_uploader("Upload Otodata Tank Levels (CSV)", type="csv")

    if telemetry_file:
        df = pd.read_csv(telemetry_file)
        city_col = find_column(df, ["City", "Town", "Location", "Ship To"])
        ullage_col = find_column(df, ["Ullage", "Room", "Volume"])
        dte_col = find_column(df, ["DTE", "Days to Empty"])
        
        if city_col:
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
            df['DTE_Val'] = df[dte_col].apply(get_days_num) if dte_col else 999
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)

            # GEO-FENCE & SCORING
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(target in x for target in target_cities))
            pool = df[(df['In_Zone'] == True) | (df['DTE_Val'] <= 1)].copy()
            
            def score_efficiency(row):
                if row['DTE_Val'] <= 1: return 1
                if row
