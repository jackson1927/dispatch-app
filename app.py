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
    show_debug = st.checkbox("🛠️ Debug: Show Column Names")

# --- APP TABS ---
tab1, tab2, tab3 = st.tabs(["🎯 Dispatch Control", "⚖️ Route Comparison", "📈 Analytics"])

with tab1:
    st.title("🚚 Propane Dispatch Console")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader("1. Data Input")
        telemetry_file = st.file_uploader("Upload Otodata Export (CSV)", type="csv", key="main_upload")
        
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

        name_col = find_col(df, ["Name", "Customer", "Account"])
        city_col = find_col(df, ["City", "Town", "Location"])
        level_col = find_col(df, ["Level", "%", "Percent", "Value", "Reading", "Tank"]) 
        ullage_col = find_col(df, ["Ullage", "Room", "Volume", "Fill"])
        dte_col = find_col(df, ["DTE", "Days to Empty", "Estimate"])

        if not name_col or not city_col:
            st.error("❌ Missing Name or City columns.")
        else:
            df['City_Clean'] = df[city_col].fillna("UNKNOWN").apply(lambda x: str(x).strip().upper())
            df['DTE_Val'] = df[dte_col].apply(lambda x: int(re.findall(r'\d+', str(x))[0]) if re.findall(r'\d+', str(x)) else 999)
            df['Ullage_Num'] = pd.to_numeric(df[ullage_col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0)
            df['Level_Disp'] = df[level_col].astype(str) if level_col else "N/A"

            # --- AI ALLOCATION LOGIC ---
            df['In_Zone'] = df['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            pool = df[(df['In_Zone'] == True) | (df['DTE_Val'] <= 1)].copy()
            pool['Score'] = pool.apply(lambda r: 1 if r['DTE_Val'] <= 1 else (2 if r['In_Zone'] and r['DTE_Val'] <= 4 else 3), axis=1)
            sorted_pool = pool.sort_values(['Score', 'DTE_Val', 'Ullage_Num'], ascending=[True, True, False])

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
                    final_route_data.append(pd.DataFrame(manifest_list))

            # --- DISPLAY AI ROUTES ---
            if final_route_data:
                st.subheader("3. AI Generated Manifests")
                m_cols = st.columns(len(active_trucks))
                for i, (t_name, _) in enumerate(active_trucks.items()):
                    with m_cols[i]:
                        t_df_list = [d for d in final_route_data if d['Assigned_Truck'].iloc[0] == t_name]
                        if t_df_list:
                            t_df = t_df_list[0]
                            st.success(f"**{t_name}**")
                            st.dataframe(t_df[[name_col, city_col, 'Level_Disp', 'Ullage_Num']].sort_values(city_col), use_container_width=True, hide_index=True)

with tab2:
    st.header("⚖️ AI vs. Manual Comparison")
    st.write("Upload your manually planned route to see how it compares to the AI's efficiency.")
    
    manual_file = st.file_uploader("Upload Manual Route (CSV)", type="csv", key="manual_upload")
    
    if telemetry_file and manual_file:
        m_df_raw = pd.read_csv(manual_file)
        # Find matches in the master telemetry data based on Name
        manual_names = m_df_raw[find_col(m_df_raw, ["Name", "Customer"])].tolist()
        manual_route = df[df[name_col].isin(manual_names)].copy()
        
        # Calculate AI Stats
        ai_all = pd.concat(final_route_data) if final_route_data else pd.DataFrame()
        
        c1, c2 = st.columns(2)
        
        with c1:
            st.metric("AI Route Stops", len(ai_all))
            st.metric("AI Zone Efficiency", f"{(ai_all['In_Zone'].mean()*100):.1f}% in zone")
            st.write("**AI Stops:**")
            st.dataframe(ai_all[[name_col, city_col]], hide_index=True)

        with c2:
            st.metric("Manual Route Stops", len(manual_route))
            # Check zone adherence for manual route
            manual_route['In_Zone'] = manual_route['City_Clean'].apply(lambda x: any(t in x for t in target_cities))
            st.metric("Manual Zone Efficiency", f"{(manual_route['In_Zone'].mean()*100):.1f}% in zone")
            st.write("**Manual Stops:**")
            st.dataframe(manual_route[[name_col, city_col]], hide_index=True)
            
        st.info("💡 A higher 'Zone Efficiency' means fewer miles driven outside your primary service area for the day.")

with tab3:
    st.header("📊 Zone Performance Analysis")
    if os.path.isfile(LOG_FILE):
        history = pd.read_csv(LOG_FILE)
        st.bar_chart(history['City_Clean'].value_counts())
    else:
        st.info("No memory data yet.")
