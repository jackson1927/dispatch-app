import streamlit as st
import pandas as pd
import re
import datetime

# --- DEFAULT CONFIG (You can change these in the app UI now!) ---
DEFAULT_ZONES = {
    "Monday": "ANNA MARIA, SARASOTA, ST PETE, HOLMES BEACH, BRADENTON BEACH, LONGBOAT KEY",
    "Tuesday": "LAKELAND, HAINES CITY, POLK CITY, DAVENPORT, WINTER HAVEN, NEW PORT RICHEY, TAMPA, HUDSON, ALVA",
    "Wednesday": "TAMPA, BRADENTON, SARASOTA, RUSKIN, PALMETTO, SUN CITY CENTER, PARRISH",
    "Thursday": "TAMPA, BRANDON, PLANT CITY, VALRICO, RIVERVIEW, SEFFNER, DOVER",
    "Friday": "ORLANDO, KISSIMMEE, LAKELAND, HAINES CITY, AUBURNDALE, CLERMONT"
}

TRUCKS = {"Truck 225": 4160, "Truck 224": 2800, "Truck 108": 2240}

st.set_page_config(page_title="Propane Dispatch Pro", layout="wide")
st.title("🚚 Dynamic Zone Dispatcher")

# --- SIDEBAR: ZONE MANAGER ---
st.sidebar.header("🗺️ Zone Manager")
st.sidebar.info("Edit cities below (separate by commas). The app will prioritize these for the selected day.")

current_zones = {}
for day, cities in DEFAULT_ZONES.items():
    current_zones[day] = st.sidebar.text_area(f"{day} Cities", value=cities, height=68)

today_name = datetime.datetime.now().strftime("%A")
route_day = st.sidebar.selectbox("Select Active Route Day", list(current_zones.keys()), index=list(current_zones.keys()).index(today_name))

# Convert the text area input into a clean list of uppercase cities
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
    dto_col = find_column(df, ["DTO"])

    if not city_col:
        st.error("❌ City column missing in CSV.")
    else:
        # Data Cleaning
        df['City_Clean'] = df[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
        df['DTE_Val'] = df[dte_col].apply(get_days_num) if dte_col else 999
        df['DTO_Val'] = df[dto_col].apply(get_days_num) if dto_col else 999
        df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)

        # --- GEO-FENCE & SCORING ---
        # Logic: Is the city in the text box for the selected day?
        df['In_Zone'] = df['City_Clean'].apply(lambda x: any(target in x for target in target_cities))
        
        # Only process people IN ZONE or DTE 0/1 (Emergencies)
        pool = df[(df['In_Zone'] == True) | (df['DTE_Val'] <= 1)].copy()
        
        def score_efficiency(row):
            if row['DTE_Val'] <= 1: return 1  # Emergency (Anywhere)
            if row['In_Zone'] and row['DTE_Val'] <= 4: return 2 # Priority Zone Low
            if row['In_Zone'] and row['Ullage_Num'] > 150: return 3 # Zone Fill-up
            return 4

        pool['Score'] = pool.apply(score_efficiency, axis=1)
        sorted_pool = pool.sort_values(['Score', 'DTE_Val'], ascending=[True, True])

        # --- ALLOCATION ---
        st.header(f"Manifests for {route_day}")
        st.write(f"**Target Cities:** {', '.join(target_cities)}")
        
        active_trucks = {name: cap for name, cap in TRUCKS.items() if st.sidebar.checkbox(name, value=True)}
        
        for t_name, t_cap in active_trucks.items():
            load = 0
            manifest = []
            for idx, row in sorted_pool.iterrows():
                if load + row['Ullage_Num'] <= t_cap:
                    load += row['Ullage_Num']
                    manifest.append(row)
                    sorted_pool = sorted_pool.drop(idx)
            
            if manifest:
                m_df = pd.DataFrame(manifest)
                with st.expander(f"📖 {t_name} - {load:.0f} gal total", expanded=True):
                    # Sort display by city to keep driver grouped
                    st.dataframe(m_df[[city_col, 'Ullage_Num', 'DTE_Val', 'DTO_Val', 'Score']].sort_values(city_col))
                    st.download_button(f"Export {t_name} CSV", m_df.to_csv(index=False).encode('utf-8'), f"{t_name}.csv")
