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

        # FIXED: Added the missing colon and structured the logic correctly
        if not name_col or not city_col:
            st.error("❌ Required columns (Name/City) missing. Check your CSV.")
        else:
            # Data Cleaning
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
            
            def safe_get_dte(val):
                nums = re.findall(r'\d+', str(val))
                return int(nums[0]) if nums else 999
                
            df['DTE_Val'] = df[dte_col].apply(safe_get_dte)
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)

            # --- POOLING & SCORING ---
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            pool = df[(df['In_Zone'] == True) | (df['DTE_Val'] <= 1)].copy()
            
            def score_row(row):
                if row['DTE_Val'] <= 1: return 1
                if row['In_Zone'] and row['DTE_Val'] <= 4: return 2
                return 3

            pool['Score'] = pool.apply(score_row, axis=1)
            sorted_pool = pool.sort_values(['Score', 'DTE_Val', 'Ullage_Num'], ascending=[True, True, False])

            # --- ALLOCATION ---
            final_route_data = []
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

            # --- DISPLAY ---
            st.markdown("---")
            if final_route_data:
                total_gals = sum(d['Ullage_Num'].sum() for d in final_route_data)
                total_stops = sum(len(d) for d in final_route_data)
                
                s1, s2, s3 = st.columns(3)
                s1.metric("Scheduled Gallons", f"{total_gals:,.0f}")
                s2.metric("Scheduled Stops", f"{total_stops}")
                s3.metric("Remaining in Pool", f"{len(temp_pool)}")

                st.subheader("3. Daily Manifests")
                m_cols = st.columns(len(active_trucks)) if active_trucks else st.columns(1)
                
                for i, (t_name, _) in enumerate(active_trucks.items()):
                    with m_cols[i]:
                        t_df_list = [d for d in final_route_data if d['Assigned_Truck'].iloc[0] == t_name]
                        if t_df_list:
                            t_df = t_df_list[0]
                            st.success(f"**{t_name}**")
                            display_cols = [name_col, city_col, 'Ullage_Num', dte_col]
                            st.dataframe(t_df[display_cols].sort_values(city_col), use_container_width=True, hide_index=True)
                            csv_data = t_df.to_csv(index=False).encode('utf-8')
                            st.download_button(f"📥 Export {t_name}", csv_data, f"{t_name}.csv", key=f"dl_{t_name}")
                        else:
                            st.warning(f"**{t_name}**: No assignments.")

                st.markdown("---")
                if st.button("🚀 FINALIZE & RECORD ALL ROUTES", use_container_width=True):
                    full_log = pd.concat(final_route_data)
                    log_entry = full_log[['Dispatch_Date', 'Dispatch_Day', 'City_Clean', 'Ullage_Num']]
                    if not os.path.isfile(LOG_FILE): log_entry.to_csv(LOG_FILE, index=False)
                    else: log_entry.to_csv(LOG_FILE, mode='a', header=False, index=False)
                    st.balloons()
                    st.success("Routes recorded in Zone Analysis memory!")
            else:
                st.warning("No customers matched. Adjust zones or check for Emergency/Optimal Fill criteria.")

with tab2:
    st.header("📊 Zone Performance Analysis")
    if os.path.isfile(LOG_FILE):
        history = pd.read_csv(LOG_FILE)
        st.write("### Deliveries per City")
        st.bar_chart(history['City_Clean'].value_counts())
    else:
        st.info("No data in memory yet. Finalize a route to see analysis.")
