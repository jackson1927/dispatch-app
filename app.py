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

# --- CUSTOM CSS FOR CLEAN LOOK ---
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #007bff; color: white; }
    .zone-box { padding: 20px; border: 1px solid #e6e9ef; border-radius: 10px; background-color: white; }
    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR: SETTINGS ---
with st.sidebar:
    st.header("⚙️ Dispatch Settings")
    max_stops = st.slider("Max Stops Per Truck", 5, 40, 22)
    st.markdown("---")
    st.subheader("🚚 Active Fleet")
    active_trucks = {name: cap for name, cap in TRUCKS.items() if st.checkbox(name, value=True)}
    st.markdown("---")
    st.info("💡 Pro-Tip: Finalize routes at the end of the morning to save data to Memory.")

# --- APP TABS ---
tab1, tab2 = st.tabs(["🎯 Dispatch Control", "📈 Analytics & Memory"])

with tab1:
    # --- HEADER SECTION ---
    st.title("🚚 Propane Dispatch Console")
    
    # --- ACTION AREA (UPPER) ---
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("1. Data Input")
        telemetry_file = st.file_uploader("Upload Otodata Export (CSV)", type="csv", help="Upload the tank level report here.")
        
    with col2:
        st.subheader("2. Zone Management")
        today_name = datetime.datetime.now().strftime("%A")
        route_day = st.selectbox("Active Day", list(DEFAULT_ZONES.keys()), index=list(DEFAULT_ZONES.keys()).index(today_name))
        
        # Compact Zone Editor
        current_zone_text = st.text_area("Edit Cities for Today", value=DEFAULT_ZONES[route_day], height=70)
        target_cities = [c.strip().upper() for c in current_zone_text.split(",") if c.strip()]

    # --- PROCESSING ---
    if telemetry_file:
        df = pd.read_csv(telemetry_file)
        # (Finding columns logic remains the same)
        def find_col(df, names):
            for c in df.columns:
                if any(n.lower() in str(c).lower() for n in names): return c
            return None

        city_col = find_col(df, ["City", "Town", "Location"])
        ullage_col = find_col(df, ["Ullage", "Room", "Volume"])
        dte_col = find_col(df, ["DTE", "Days to Empty"])

        if city_col:
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
            df['DTE_Val'] = df[dte_col].apply(lambda x: int(re.findall(r'\d+', str(x))[0]) if re.findall(r'\d+', str(x)) else 999)
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)

            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            pool = df[(df['In_Zone']) | (df['DTE_Val'] <= 1)].copy()
            pool['Score'] = pool.apply(lambda r: 1 if r['DTE_Val'] <= 1 else (2 if r['In_Zone'] and r['DTE_Val'] <= 4 else 3), axis=1)
            sorted_pool = pool.sort_values(['Score', 'DTE_Val', 'Ullage_Num'], ascending=[True, True, False])

            # --- STATS BAR ---
            st.markdown("---")
            stat_col1, stat_col2, stat_col3 = st.columns(3)
            
            final_route_data = []
            assigned_total_gals = 0
            assigned_total_stops = 0

            # Pre-calculation for Stats
            temp_pool = sorted_pool.copy()
            for t_name, t_cap in active_trucks.items():
                load, count = 0, 0
                manifest_list = []
                for idx, row in temp_pool.iterrows():
                    if (load + row['Ullage_Num'] <= t_cap) and (count < max_stops):
                        load += row['Ullage_Num']
                        count += 1
                        r = row.copy()
                        r['Assigned_Truck'] = t_name
                        manifest_list.append(r)
                        temp_pool = temp_pool.drop(idx)
                if manifest_list:
                    m_df = pd.DataFrame(manifest_list)
                    m_df['Dispatch_Date'] = datetime.date.today()
                    m_df['Dispatch_Day'] = route_day
                    final_route_data.append(m_df)
                    assigned_total_gals += load
                    assigned_total_stops += count

            stat_col1.metric("Total Scheduled Gallons", f"{assigned_total_gals:,.0f} gal")
            stat_col2.metric("Total Stops", f"{assigned_total_stops}")
            stat_col3.metric("Pool Remaining", f"{len(temp_pool)} tanks")

            # --- MANIFEST DISPLAY ---
            st.subheader("3. Daily Manifests")
            m_cols = st.columns(len(active_trucks))
            
            for i, (t_name, t_cap) in enumerate(active_trucks.items()):
                with m_cols[i]:
                    # Find the specific dataframe for this truck
                    t_df_list = [d for d in final_route_data if d['Assigned_Truck'].iloc[0] == t_name]
                    if t_df_list:
                        t_df = t_df_list[0]
                        st.success(f"**{t_name}**")
                        st.write(f"📈 {len(t_df)} stops | {t_df['Ullage_Num'].sum():.0f} gal")
                        st.dataframe(t_df[[city_col, 'Ullage_Num', 'DTE_Val']].sort_values(city_col), use_container_width=True, hide_index=True)
                        csv = t_df.to_csv(index=False).encode('utf-8')
                        st.download_button(f"📥 {t_name} CSV", csv, f"{t_name}.csv", key=t_name)
                    else:
                        st.warning(f"**{t_name}**\nNo stops assigned.")

            # --- FINALIZE AREA ---
            st.markdown("---")
            if final_route_data:
                if st.button("🚀 FINALIZE & RECORD ALL ROUTES"):
                    full_log = pd.concat(final_route_data)
                    log_entry = full_log[['Dispatch_Date', 'Dispatch_Day', 'City_Clean', 'Ullage_Num']]
                    if not os.path.isfile(LOG_FILE): log_entry.to_csv(LOG_FILE, index=False)
                    else: log_entry.to_csv(LOG_FILE, mode='a', header=False, index=False)
                    st.balloons()

# --- TAB 2: ANALYSIS ---
with tab2:
    st.header("📊 Zone Performance Analysis")
    if os.path.isfile(LOG_FILE):
        history = pd.read_csv(LOG_FILE)
        c1, c2 = st.columns(2)
        with c1: st.bar_chart(history['City_Clean'].value_counts())
        with c2: st.bar_chart(history.groupby('City_Clean')['Ullage_Num'].sum())
    else:
        st.info("No data in memory.")
