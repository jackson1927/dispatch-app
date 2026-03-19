import streamlit as st
import pandas as pd
import re
import io

# --- CONFIGURATION ---
TRUCKS = {
    "Truck 225": 4160,
    "Truck 224": 2800,
    "Truck 108": 2240
}

st.set_page_config(page_title="Propane Auto-Dispatch", layout="wide")
st.title("🚚 Propane Auto-Dispatch System")
st.markdown("Upload your daily telemetry and delivery history to generate optimized routes.")

# --- HELPERS ---
def clean_acct(val):
    if pd.isna(val): return ""
    match = re.search(r'(\d+)', str(val))
    return match.group(1).lstrip('0') if match else ""

def parse_val(val):
    try:
        return float(str(val).replace(',', '').replace('gal', '').strip())
    except:
        return 0.0

# --- SIDEBAR SETTINGS ---
st.sidebar.header("Truck Availability")
active_trucks = {}
for name, cap in TRUCKS.items():
    if st.sidebar.checkbox(name, value=True):
        new_cap = st.sidebar.number_input(f"{name} Capacity", value=cap)
        active_trucks[name] = new_cap

# --- FILE UPLOADS ---
col1, col2 = st.columns(2)
with col1:
    telemetry_file = st.file_uploader("Upload Otodata Export (CSV)", type="csv")
with col2:
    history_file = st.file_uploader("Upload Last 5 Deliveries (CSV)", type="csv")

if telemetry_file and history_file:
    df_tel = pd.read_csv(telemetry_file)
    df_hist = pd.read_csv(history_file)

    # Clean Data
    df_tel['Acct_Key'] = df_tel['Account number'].apply(clean_acct)
    df_hist['Acct_Key'] = df_hist['Account Number'].apply(clean_acct)
    
    # Calculate History Avg
    qty_cols = ['qty1', 'qty2', 'qty3', 'qty4', 'qty5']
    for col in qty_cols:
        df_hist[col] = pd.to_numeric(df_hist[col], errors='coerce').fillna(0)
    df_hist['Avg_Drop'] = df_hist[qty_cols].replace(0, pd.NA).mean(axis=1).fillna(0)
    hist_lookup = df_hist.groupby('Acct_Key')['Avg_Drop'].first().reset_index()

    # Merge and Logic
    df_tel['Ullage_Val'] = df_tel['Ullage'].apply(parse_val)
    master = pd.merge(df_tel, hist_lookup, on='Acct_Key', how='left')
    
    # Smart Ullage Logic
    master['Planned_Gallons'] = master.apply(
        lambda x: min(x['Ullage_Val'], x['Avg_Drop']) if x['Avg_Drop'] > 0 else x['Ullage_Val'], axis=1
    )

    # Priority Scoring (DTO 0-1 = High Priority)
    def get_priority(dto_str):
        dto_str = str(dto_str).lower()
        if 'hour' in dto_str or 'minute' in dto_str: return 0
        nums = re.findall(r'\d+', dto_str)
        return int(nums[0]) if nums else 999

    master['Priority'] = master['DTO'].apply(get_priority)
    pool = master[master['Planned_Gallons'] > 30].sort_values(['Priority', 'Planned_Gallons'], ascending=[True, False])

    # --- ALLOCATION ---
    st.header("Generated Routes")
    final_routes = []
    
    for t_name, t_cap in active_trucks.items():
        current_load = 0
        assigned = []
        for idx, row in pool.iterrows():
            if current_load + row['Planned_Gallons'] <= t_cap:
                current_load += row['Planned_Gallons']
                row['Assigned_Truck'] = t_name
                final_routes.append(row)
                assigned.append(idx)
        pool = pool.drop(assigned)

    if final_routes:
        output_df = pd.DataFrame(final_routes)
        for t_name in active_trucks.keys():
            t_route = output_df[output_df['Assigned_Truck'] == t_name]
            st.subheader(f"{t_name} - {t_route['Planned_Gallons'].sum():.0f} Gallons")
            st.dataframe(t_route[['Account number', 'Customer Name', 'City', 'Planned_Gallons', 'DTO']])
            
            # Download Button
            csv = t_route.to_csv(index=False).encode('utf-8')
            st.download_button(f"Download {t_name} Route", csv, f"{t_name}_route.csv", "text/csv")
