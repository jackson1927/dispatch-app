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
