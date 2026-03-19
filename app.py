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
                if row['In_Zone'] and row['DTE_Val'] <= 4: return 2
                return 3

            pool['Score'] = pool.apply(score_efficiency, axis=1)
            sorted_pool = pool.sort_values(['Score', 'DTE_Val', 'Ullage_Num'], ascending=[True, True, False])

            # ALLOCATION
            st.header(f"Manifests for {route_day}")
            active_trucks = {name: cap for name, cap in TRUCKS.items() if st.sidebar.checkbox(name, value=True)}
            
            all_scheduled_stops = []

            for t_name, t_cap in active_trucks.items():
                load = 0
                stop_count = 0
                manifest = []
                
                # Allocation Loop
                for idx, row in sorted_pool.iterrows():
                    if (load + row['Ullage_Num'] <= t_cap) and (stop_count < max_stops):
                        load += row['Ullage_Num']
                        stop_count += 1
                        row_copy = row.copy()
                        row_copy['Assigned_Truck'] = t_name
                        row_copy['Dispatch_Date'] = datetime.date.today()
                        row_copy['Dispatch_Day'] = route_day
                        manifest.append(row_copy)
                        all_scheduled_stops.append(row_copy)
                        sorted_pool = sorted_pool.drop(idx)
                
                if manifest:
                    m_df = pd.DataFrame(manifest)
                    with st.expander(f"📖 {t_name} | {stop_count} Stops | {load:.0f} Gal", expanded=True):
                        st.dataframe(m_df[[city_col, 'Ullage_Num', 'DTE_Val']].sort_values(city_col))
                        csv_data = m_df.to_csv(index=False).encode('utf-8')
                        st.download_button(label=f"Download {t_name} CSV", data=csv_data, file_name=f"{t_name}_{route_day}.csv")

            # --- MEMORY LOGGING ---
            if all_scheduled_stops:
                log_df = pd.DataFrame(all_scheduled_stops)
                log_entry = log_df[['Dispatch_Date', 'Dispatch_Day', 'City_Clean', 'Ullage_Num']]
                if not os.path.isfile(LOG_FILE):
                    log_entry.to_csv(LOG_FILE, index=False)
                else:
                    log_entry.to_csv(LOG_FILE, mode='a', header=False, index=False)
                st.success(f"📝 Memory Updated: {len(all_scheduled_stops)} stops recorded.")

with tab2:
    st.header("📊 Zone Performance Analysis")
    if os.path.isfile(LOG_FILE):
        history = pd.read_csv(LOG_FILE)
        st.write("### Deliveries per City")
        city_counts = history['City_Clean'].value_counts().reset_index()
        city_counts.columns = ['City', 'Stops']
        st.bar_chart(city_counts.set_index('City'))
        
        if st.button("Clear Memory Log"):
            os.remove(LOG_FILE)
            st.rerun()
    else:
        st.info("Run a route to start tracking trends.")
