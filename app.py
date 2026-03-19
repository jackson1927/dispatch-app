import streamlit as st
import pandas as pd
import re
import datetime

# --- CONFIGURATION ---
TRUCKS = {"Truck 225": 4160, "Truck 224": 2800, "Truck 108": 2240}
ZONES = {
    "Monday": ["ANNA MARIA", "SARASOTA", "ST PETE", "ST. PETERSBURG", "TAMPA", "HOLMES BEACH", "BRADENTON BEACH"],
    "Tuesday": ["LAKELAND", "HAINES CITY", "POLK CITY", "DAVENPORT", "WINTER HAVEN", "NEW PORT RICHEY", "TAMPA", "HUDSON"],
    "Wednesday": ["TAMPA", "BRADENTON", "SARASOTA", "RUSKIN", "PALMETTO", "SUN CITY CENTER"],
    "Thursday": ["TAMPA", "BRANDON", "PLANT CITY", "VALRICO", "RIVERVIEW"],
    "Friday": ["ORLANDO", "KISSIMMEE", "LAKELAND", "HAINES CITY", "AUBURNDALE"]
}

st.set_page_config(page_title="Propane Auto-Dispatch Pro", layout="wide")
st.title("🚚 Smart-Zone Dispatcher")

def find_column(df, possible_names):
    for col in df.columns:
        if any(name.lower() in str(col).lower() for name in possible_names):
            return col
    return None

def get_dto_num(val):
    val = str(val).lower()
    if 'hour' in val or 'minute' in val: return 0
    nums = re.findall(r'\d+', val)
    return int(nums[0]) if nums else 999

# --- SIDEBAR ---
st.sidebar.header("Route Settings")
today_name = datetime.datetime.now().strftime("%A")
route_day = st.sidebar.selectbox("Select Route Day", list(ZONES.keys()), index=list(ZONES.keys()).index(today_name))
target_cities = ZONES[route_day]

active_trucks = {name: st.sidebar.number_input(f"{name} Cap", value=cap) 
                 for name, cap in TRUCKS.items() if st.sidebar.checkbox(name, value=True)}

# --- FILE UPLOADS ---
telemetry_file = st.file_uploader("Upload Otodata Export (CSV)", type="csv")

if telemetry_file:
    df = pd.read_csv(telemetry_file)
    
    # DYNAMIC COLUMN MATCHING
    city_col = find_column(df, ["City", "Town", "Location", "Ship To"])
    name_col = find_column(df, ["Name", "Customer"])
    ullage_col = find_column(df, ["Ullage", "Room", "Volume"])
    dto_col = find_column(df, ["DTO", "Days to Empty", "Days"])

    if not city_col:
        st.error(f"❌ Could not find a 'City' column. Headers found: {df.columns.tolist()}")
    else:
        # DATA CLEANING
        df['City_Clean'] = df[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
        df['DTO_Num'] = df[dto_col].apply(get_dto_num) if dto_col else 999
        df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0) if ullage_col else 0

        # SCORING
        def score_row(row):
            if row['DTO_Num'] <= 1: return 1 
            if any(target.upper() in row['City_Clean'] for target in target_cities) and row['Ullage_Num'] > 150: return 2
            if any(target.upper() in row['City_Clean'] for target in target_cities): return 3
            if row['DTO_Num'] <= 3: return 4
            return 5

        df['Route_Priority'] = df.apply(score_row, axis=1)
        pool = df[df['Ullage_Num'] > 40].sort_values(['Route_Priority', 'Ullage_Num'], ascending=[True, False])

        # ALLOCATION
        st.header(f"Routes for {route_day}")
        final_output = []
        for t_name, t_cap in active_trucks.items():
            load = 0
            assigned_indices = []
            for idx, row in pool.iterrows():
                if load + row['Ullage_Num'] <= t_cap:
                    load += row['Ullage_Num']
                    row_dict = row.to_dict()
                    row_dict['Assigned_Truck'] = t_name
                    final_output.append(row_dict)
                    assigned_indices.append(idx)
            pool = pool.drop(assigned_indices)

        if final_output:
            res_df = pd.DataFrame(final_output)
            for t_name in active_trucks.keys():
                t_df = res_df[res_df['Assigned_Truck'] == t_name]
                if not t_df.empty:
                    with st.expander(f"📖 {t_name} Manifest - Total: {t_df['Ullage_Num'].sum():.0f} gal", expanded=True):
                        display_cols = [c for c in [name_col, city_col, ullage_col, 'DTO'] if c]
                        st.dataframe(t_df[display_cols].sort_values(city_col))
                        csv = t_df.to_csv(index=False).encode('utf-8')
                        st.download_button(f"Download {t_name} CSV", csv, f"{t_name}_{route_day}.csv")
