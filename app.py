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
st.title("🚚 Smart-Zone Dispatcher (DTO + DTE Aware)")

# --- HELPERS ---
def find_column(df, possible_names):
    for col in df.columns:
        if any(name.lower() in str(col).lower() for name in possible_names):
            return col
    return None

def get_days_num(val):
    val = str(val).lower()
    if any(x in val for x in ['hour', 'minute', 'now', '0']): return 0
    nums = re.findall(r'\d+', val)
    return int(nums[0]) if nums else 999

# --- SIDEBAR ---
st.sidebar.header("Route Settings")
today_name = datetime.datetime.now().strftime("%A")
route_day = st.sidebar.selectbox("Select Route Day", list(ZONES.keys()), index=list(ZONES.keys()).index(today_name))
target_cities = ZONES[route_day]

# --- FILE UPLOADS ---
col1, col2 = st.columns(2)
with col1:
    telemetry_file = st.file_uploader("1. Upload Otodata Tank Levels (CSV)", type="csv")
with col2:
    history_file = st.file_uploader("2. Upload Delivery History (CSV) - Optional", type="csv")

if telemetry_file:
    df_tel = pd.read_csv(telemetry_file)
    
    city_col = find_column(df_tel, ["City", "Town", "Location", "Ship To"])
    name_col = find_column(df_tel, ["Name", "Customer"])
    ullage_col = find_column(df_tel, ["Ullage", "Room", "Volume"])
    dto_col = find_column(df_tel, ["DTO"])
    dte_col = find_column(df_tel, ["DTE", "Days to Empty"])

    if not city_col:
        st.error(f"❌ Error: 'City' column not found. Headers: {df_tel.columns.tolist()}")
    else:
        df_tel['City_Clean'] = df_tel[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
        df_tel['DTO_Val'] = df_tel[dto_col].apply(get_days_num) if dto_col else 999
        df_tel['DTE_Val'] = df_tel[dte_col].apply(get_days_num) if dte_col else 999
        df_tel['Ullage_Num'] = pd.to_numeric(df_tel[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0) if ullage_col else 0

        # --- DTO & DTE SCORING LOGIC ---
        def score_row(row):
            is_in_zone = any(target.upper() in row['City_Clean'] for target in target_cities)
            
            # Priority 1: EMERGENCY (Out of gas or 1 day left)
            if row['DTO_Val'] <= 1 or row['DTE_Val'] <= 1: return 1
            
            # Priority 2: ZONE STRATEGY (In today's zone and getting low)
            if is_in_zone and (row['DTO_Val'] <= 4 or row['DTE_Val'] <= 4): return 2
            
            # Priority 3: BIG DROPS (In zone, plenty of room)
            if is_in_zone and row['Ullage_Num'] > 200: return 3
            
            # Priority 4: OFF-ZONE LOW (DTE/DTO 2-3 but wrong area)
            if row['DTO_Val'] <= 3 or row['DTE_Val'] <= 3: return 4
            
            return 5

        df_tel['Priority_Score'] = df_tel.apply(score_row, axis=1)
        pool = df_tel[df_tel['Ullage_Num'] > 40].sort_values(['Priority_Score', 'DTO_Val', 'DTE_Val'], ascending=[True, True, True])

        # ALLOCATION
        st.header(f"Smart Manifests: {route_day}")
        final_output = []
        truck_list = {name: st.sidebar.number_input(f"{name} Cap", value=cap) for name, cap in TRUCKS.items() if st.sidebar.checkbox(name, value=True)}
        
        for t_name, t_cap in truck_list.items():
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
            for t_name in truck_list.keys():
                t_df = res_df[res_df['Assigned_Truck'] == t_name]
                if not t_df.empty:
                    with st.expander(f"📋 {t_name} - {t_df['Ullage_Num'].sum():.0f} gal", expanded=True):
                        disp = t_df[[name_col, city_col, 'Ullage_Num', 'DTO_Val', 'DTE_Val', 'Priority_Score']].sort_values(city_col)
                        st.dataframe(disp)
                        csv = t_df.to_csv(index=False).encode('utf-8')
                        st.download_button(f"Export {t_name} CSV", csv, f"{t_name}_{route_day}.csv")
