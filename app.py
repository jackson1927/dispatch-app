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

st.set_page_config(page_title="Propane Dispatch AI", layout="wide", initial_sidebar_state="expanded")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Controls")
    max_stops = st.slider("Max Stops Per Truck", 5, 40, 22)
    st.markdown("---")
    st.subheader("🚚 Active Fleet")
    active_trucks = {name: cap for name, cap in TRUCKS.items() if st.checkbox(name, value=True)}

# --- APP TABS ---
tab1, tab2 = st.tabs(["🎯 Dispatch Control", "📈 Analytics"])

with tab1:
    st.title("🚚 Propane Dispatch Console")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader("1. Data Input")
        telemetry_file = st.file_uploader("Upload Otodata Export (CSV)", type="csv")
        
    with col2:
        st.subheader("2. Zone Management")
        today_name = datetime.datetime.now().strftime("%A")
        route_day = st.selectbox("Active Day", list(DEFAULT_ZONES.keys()), index=list(DEFAULT_ZONES.keys()).index(today_name))
        current_zone_text = st.text_area("Edit Cities", value=DEFAULT_ZONES[route_day], height=70)
        target_cities = [c.strip().upper() for c in current_zone_text.split(",") if c.strip()]

    if telemetry_file:
        df = pd.read_csv(telemetry_file)
        
        def find_col(df, names):
            for c in df.columns:
                if any(n.lower() in str(c).lower() for n in names): return c
            return None

        name_col = find_col(df, ["Name", "Customer", "Account Name"])
        city_col = find_col(df, ["City", "Town", "Location"])
        ullage_col = find_col(df, ["Ullage", "Room", "Volume"])
        dte_col = find_col(df, ["DTE", "Days to Empty"])

        if not name_col or not city_col:
            st.error("❌ Required columns (Name/City) missing. Check your CSV.")
        else
