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

# --- AUTO-DETECTION ENGINE ---
def find_col(df, keywords):
    """Searches for columns that contain any of the keywords (case-insensitive)."""
    for col in df.columns:
        if any(key.lower() in str(col).lower() for key in keywords):
            return col
    return None

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Settings")
    max_stops = st.slider("Max Stops Per Truck", 5, 40, 22)
    active_trucks = {name: cap for name, cap in TRUCKS.items() if st.checkbox(name, value=True)}
    st.markdown("---")
    if st.button("🗑️ Reset Memory"):
        if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
        st.rerun()

# --- APP TABS ---
tab1, tab2 = st.tabs(["🎯 Dispatcher", "📊 Zone Analysis"])

with tab1:
    st.title("🚚 Propane Dispatcher")
    
    # 1. DATA INPUT
    telemetry_file = st.file_uploader("Upload Otodata CSV", type="csv")
    
    # 2. ZONE SELECTION
    today_name = datetime.datetime.now().strftime("%A")
    route_day = st.selectbox("Select Route Day", list(DEFAULT_ZONES.keys()), index=list(DEFAULT_ZONES.keys()).index(today_name))
    target_cities = [c.strip().upper() for c in st.text_area("Cities in Zone", DEFAULT_ZONES[route_day]).split(",") if c.strip()]

    if telemetry_file:
        # Load data with a fallback for weird encoding
        try:
            df = pd.read_csv(telemetry_file, encoding='utf-8')
        except:
            df = pd.read_csv(telemetry_file, encoding='latin1')

        # DETECT COLUMNS
        name_col = find_col(df, ["Name", "Customer", "Account", "Asset"])
        city_col = find_col(df, ["City", "Town", "Location", "Ship To"])
        level_col = find_col(df, ["Level", "%", "Percent", "Reading"])
        ullage_col = find_col(df, ["Ullage", "Room", "Volume", "Fill"])
        dte_col = find_col(df, ["DTE", "Days to Empty", "Estimate"])

        # VALIDATION
        if not name_col or not city_col:
            st.error(f"❌ Could not find Name or City columns. Found: {list(df.columns)}")
        else:
            # CLEANING
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").astype(str).str.strip().upper()
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)
            
            # Smart DTE Parsing
            def parse_dte(val):
                nums = re.findall(r'\d+', str(val))
                return int(nums[0]) if nums else 99
            df['DTE_Val'] = df[dte_col].apply(parse_dte) if dte_col else 99

            # LOGIC
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(target in x for target in target_cities))
            pool = df[(df['In_Zone']) | (df['DTE_Val'] <= 2)].copy()
            pool['Score'] = pool.apply(lambda r: 1 if r['DTE_Val'] <= 2 else 2, axis=1)
            sorted_pool = pool.sort_values(['Score', 'DTE_Val', 'Ullage_Num'], ascending=[True, True, False])

            # ASSIGNMENT
            final_route_data = []
            temp_pool = sorted_pool.copy()
            for t_name, t_cap in active_trucks.items():
                load, count, manifest = 0, 0, []
                for idx, row in temp_pool.iterrows():
                    if (load + row['Ullage_Num'] <= t_cap) and (count < max_stops):
                        load += row['Ullage_Num']
                        count += 1
                        row_copy = row.copy()
                        row_copy['Assigned_Truck'] = t_name
                        manifest.append(row_copy)
                        temp_pool = temp_pool.drop(idx)
                if manifest:
                    final_route_data.append(pd.DataFrame(manifest))

            # DISPLAY
            if final_route_data:
                cols = st.columns(len(active_trucks))
                for i, m_df in enumerate(final_route_data):
                    t_name = m_df['Assigned_Truck'].iloc[0]
                    with cols[i]:
                        st.success(f"**{t_name}**")
                        disp_cols = [name_col, city_col]
                        if level_col: disp_cols.append(level_col)
                        st.dataframe(m_df[disp_cols], hide_index=True)
                
                if st.button("✅ Finalize & Record", use_container_width=True):
                    all_routes = pd.concat(final_route_data)
                    all_routes['Dispatch_Day'] = route_day
                    all_routes[['Dispatch_Day', 'City_Clean', 'Ullage_Num']].to_csv(LOG_FILE, mode='a', index=False, header=not os.path.exists(LOG_FILE))
                    st.balloons()

with tab2:
    if os.path.exists(LOG_FILE):
        history = pd.read_csv(LOG_FILE)
        st.bar_chart(history.groupby('City_Clean')['Ullage_Num'].count())
    else:
        st.info("No data yet.")
