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

# --- HELPERS ---
def find_column(df, possible_names):
    for col in df.columns:
        if any(name.lower() in str(col).lower() for name in possible_names):
            return col
    return None

def get_dte_num(val):
    val = str(val).lower()
    if 'hour' in val or 'minute' in val or 'now' in val: return 0
    nums = re.findall(r'\d+', val)
    return int(nums[0]) if nums else 999

def clean_acct(val):
    if pd.isna(val): return ""
    match = re.search(r'(\d+)', str(val))
    return match.group(1).lstrip('0') if match else ""

# --- SIDEBAR ---
st.sidebar.header("Route Settings")
today_name = datetime.datetime.now().strftime("%A")
route_day = st.sidebar.selectbox("Select Route Day", list(ZONES.keys()), index=list(ZONES.keys()).index(today_name))
target_cities = ZONES[route_day]

active_trucks = {name: st.sidebar.number_input(f"{name} Cap", value=cap) 
                 for name, cap in TRUCKS.items() if st.sidebar.checkbox(name, value=True)}

# --- FILE UPLOADS ---
col1, col2 = st.columns(2)
with col1:
    telemetry_file = st.file_uploader("1. Upload Otodata Tank Levels (CSV)", type="csv")
with col2:
    history_file = st.file_uploader("2. Upload Delivery History (CSV) - Optional", type="csv")

if telemetry_file:
    df_tel = pd.read_csv(telemetry_file)
    
    city_col = find_column(df_tel, ["City", "Town", "Location", "Ship To"])
    acct_col_tel = find_column(df_tel, ["Account", "Customer Number", "Acct"])
    name_col = find_column(df_tel, ["Name", "Customer"])
    ullage_col = find_column(df_tel, ["Ullage", "Room", "Volume"])
    dte_col = find_column(df_tel, ["DTO", "DTE", "Days to Empty", "Days"])

    if not city_col:
        st.error(f"❌ Error: 'City' column not found. Headers: {df_tel.columns.tolist()}")
    else:
        df_tel['City_Clean'] = df_tel[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
        df_tel['DTE_Num'] = df_tel[dte_col].apply(get_dte_num) if dte_col else 999
        df_tel['Ullage_Num'] = pd.to_numeric(df_tel[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0) if ullage_col else 0
        df_tel['Acct_Key'] = df_tel[acct_col_tel].apply(clean_acct) if acct_col_tel else ""

        # Smart Drop Logic
        if history_file:
            df_hist = pd.read_csv(history_file)
            acct_col_hist = find_column(df_hist, ["Account", "Acct"])
            qty_cols = [c for c in df_hist.columns if 'qty' in c.lower()]
            if acct_col_hist and qty_cols:
                df_hist['Acct_Key'] = df_hist[acct_col_hist].apply(clean_acct)
                df_hist['Avg_Drop'] = df_hist[qty_cols].replace(0, pd.NA).mean(axis=1).fillna(0)
                hist_map = df_hist.groupby('Acct_Key')['Avg_Drop'].first()
                df_tel['Planned_Gals'] = df_tel.apply(lambda x: min(x['Ullage_Num'], hist_map.get(x['Acct_Key'], x['Ullage_Num'])), axis=1)
            else:
                df_tel['Planned_Gals'] = df_tel['Ullage_Num']
        else:
            df_tel['Planned_Gals'] = df_tel['Ullage_Num']

        # --- DTE & ZONE SCORING ---
        def score_row(row):
            is_in_zone = any(target.upper() in row['City_Clean'] for target in target_cities)
            # Priority 1: Critical (DTE 0-1)
            if row['DTE_Num'] <= 1: return 1
            # Priority 2: In-Zone and getting low (DTE 2-4)
            if is_in_zone and row['DTE_Num'] <= 4: return 2
            # Priority 3: In-Zone Fill-up (Good drop size)
            if is_in_zone and row['Planned_Gals'] > 175: return 3
            # Priority 4: Out-of-Zone Critical (DTE 2-3)
            if row['DTE_Num'] <= 3: return 4
            return 5

        df_tel['Priority_Score'] = df_tel.apply(score_row, axis=1)
        # Filter for anything that can take a 40gal+ drop
        pool = df_tel[df_tel['Planned_Gals'] > 40].sort_values(['Priority_Score', 'DTE_Num', 'Planned_Gals'], ascending=[True, True, False])

        # ALLOCATION
        st.header(f"Smart-Zone Routes: {route_day}")
        final_output = []
        for t_name, t_cap in active_trucks.items():
            load = 0
            assigned_indices = []
            for idx, row in pool.iterrows():
                if load + row['Planned_Gals'] <= t_cap:
                    load += row['Planned_Gals']
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
                    with st.expander(f"📖 {t_name} - {t_df['Planned_Gals'].sum():.0f} gal", expanded=True):
                        # Sort display by City so the driver is efficient
                        disp = t_df[[name_col, city_col, 'Planned_Gals', dte_col, 'Priority_Score']].sort_values(city_col)
                        st.dataframe(disp)
                        csv = t_df.to_csv(index=False).encode('utf-8')
                        st.download_button(f"Download {t_name} CSV", csv, f"{t_name}_{route_day}.csv")
