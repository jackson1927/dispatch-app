import streamlit as st
import pandas as pd
import re
import datetime

# --- CONFIGURATION ---
TRUCKS = {"Truck 225": 4160, "Truck 224": 2800, "Truck 108": 2240}

# Your Specific Route Zones
ZONES = {
    "Monday": ["ANNA MARIA", "SARASOTA", "ST PETE", "ST. PETERSBURG", "TAMPA", "HOLMES BEACH", "BRADENTON BEACH"],
    "Tuesday": ["LAKELAND", "HAINES CITY", "POLK CITY", "DAVENPORT", "WINTER HAVEN", "NEW PORT RICHEY", "TAMPA", "HUDSON"],
    "Wednesday": ["TAMPA", "BRADENTON", "SARASOTA", "RUSKIN", "PALMETTO", "SUN CITY CENTER"],
    "Thursday": ["TAMPA", "BRANDON", "PLANT CITY", "VALRICO"], # Clean up / Central
    "Friday": ["ORLANDO", "KISSIMMEE", "LAKELAND", "HAINES CITY", "AUBURNDALE"]
}

st.set_page_config(page_title="Propane Auto-Dispatch Pro", layout="wide")
st.title("🚚 Smart-Zone Dispatcher")

# --- HELPERS ---
def clean_city(val):
    return str(val).strip().upper()

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

st.sidebar.markdown(f"**Targeting:** {', '.join(target_cities[:5])}...")

active_trucks = {name: st.sidebar.number_input(f"{name} Cap", value=cap) 
                 for name, cap in TRUCKS.items() if st.sidebar.checkbox(name, value=True)}

# --- FILE UPLOADS ---
telemetry_file = st.file_uploader("Upload Otodata Export (CSV)", type="csv")

if telemetry_file:
    df = pd.read_csv(telemetry_file)
    df['City_Clean'] = df['City'].apply(clean_city)
    df['DTO_Num'] = df['DTO'].apply(get_dto_num)
    df['Ullage_Num'] = pd.to_numeric(df['Ullage'].str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)

    # SCORING LOGIC
    def score_row(row):
        # Emergency Priority
        if row['DTO_Num'] <= 1: return 1 
        # In-Zone & Needs Fuel
        if row['City_Clean'] in target_cities and row['Ullage_Num'] > 150: return 2
        # In-Zone Fill-up
        if row['City_Clean'] in target_cities: return 3
        # Out of Zone but low
        if row['DTO_Num'] <= 3: return 4
        return 5

    df['Route_Priority'] = df.apply(score_row, axis=1)
    
    # Filter out tanks that are too full to bother with
    pool = df[df['Ullage_Num'] > 40].sort_values(['Route_Priority', 'Ullage_Num'], ascending=[True, False])

    # --- ALLOCATION ---
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
            with st.expander(f"📖 {t_name} Manifest - Total: {t_df['Ullage_Num'].sum():.0f} Gallons", expanded=True):
                # Sort the manifest by City to keep the driver in one area
                t_df = t_df.sort_values('City')
                st.dataframe(t_df[['Customer Name', 'City', 'Ullage_Num', 'DTO', 'Route_Priority']])
                csv = t_df.to_csv(index=False).encode('utf-8')
                st.download_button(f"Download {t_name} CSV", csv, f"{t_name}_{route_day}.csv")
