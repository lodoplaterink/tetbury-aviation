"""
Tetbury Aviation — Weekly Fleet Dashboard (Streamlit app)
=========================================================

A point-and-click interface over the fleet utilisation tool. Pick a week,
view the schedule, utilisation and capacity position. Reads the project data
files by default; bookings/roster can also be uploaded.

RUN IT:
    streamlit run app.py
"""
from __future__ import annotations
import tempfile
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# Reuse the exact same logic as the command-line tool — single source of truth
import tetbury_fleet_tool as core

# ─────────────────────────────────────────────────────────────────────────────
# Page setup
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Tetbury Fleet Dashboard",
    layout="wide",
)

# Cool-grey corporate palette
NAVY  = "#1F2A37"
STEEL = "#3E5C76"
SKY   = "#748CAB"
PANEL = "#F1F3F5"
GRID  = "#E2E6EA"
SLATE = "#6B7280"

st.markdown(f"""
    <style>
    /* Serif typography throughout */
    html, body, [class*="css"], .stMarkdown, .stText,
    .stMetric, h1, h2, h3, h4, p, div {{
        font-family: 'Georgia', 'Times New Roman', serif !important;
    }}
    /* Cool-grey background */
    .stApp {{ background: #FAFBFC; }}
    section[data-testid="stSidebar"] {{ background: {PANEL}; }}
    /* Sidebar text must be dark on the light panel */
    section[data-testid="stSidebar"] * {{ color: {NAVY} !important; }}
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] .stRadio,
    section[data-testid="stSidebar"] p {{ color: {NAVY} !important; }}
    .block-container {{ padding-top: 2.2rem; max-width: 1400px; }}

    h1, h1 * {{
        color: {NAVY} !important;
        font-family: 'Georgia', 'Times New Roman', serif !important;
        font-weight: 700; letter-spacing: -0.01em;
    }}
    h2, h3, h4 {{ color: {NAVY} !important; }}

    /* Metric cards */
    div[data-testid="stMetric"] {{
        background: #FFFFFF;
        border: 1px solid {GRID};
        border-left: 3px solid {STEEL};
        border-radius: 6px;
        padding: 14px 18px;
        box-shadow: 0 1px 2px rgba(31,42,55,0.04);
    }}
    div[data-testid="stMetric"] * {{ color: {NAVY} !important; }}
    div[data-testid="stMetricLabel"] p {{
        color: {SLATE} !important; font-weight: 600;
        text-transform: uppercase; font-size: 0.72rem; letter-spacing: 0.04em;
    }}
    /* Warning / alert boxes: dark text so it stays readable */
    div[data-testid="stAlert"] * {{ color: {NAVY} !important; }}
    /* Tabs */
    button[data-baseweb="tab"] {{ font-family: Georgia, serif !important; }}
    button[data-baseweb="tab"] * {{ color: {NAVY} !important; }}
    button[data-baseweb="tab"][aria-selected="true"] * {{ color: {STEEL} !important; }}
    div[data-baseweb="tab-list"] {{ border-bottom: 1px solid {GRID}; }}
    /* Section rule under title */
    hr {{ border-color: {GRID}; }}
    </style>
""", unsafe_allow_html=True)

st.title("Tetbury Dashboard")
st.caption("European football charter fleet  ·  utilisation and schedule by week")
st.markdown(f"<hr style='margin-top:0.2rem;margin-bottom:1.2rem'>",
            unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def load_from_disk():
    roster = pd.read_csv("fleet_roster.csv")
    bookings = pd.read_csv("flight_bookings.csv")
    return roster, bookings


def read_any(uploaded):
    if uploaded.name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded)
    return pd.read_csv(uploaded)


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — inputs
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("1 · Data source")
    source = st.radio(
        "Fleet & bookings data",
        ["Project data files", "Upload my own"],
        help="Project files are fleet_roster.csv and flight_bookings.csv "
             "in the app folder (exported from the R model).",
    )

    if source == "Project data files":
        try:
            roster, bookings = load_from_disk()
            st.success(f"Loaded · {len(roster)} aircraft, "
                       f"{len(bookings):,} legs")
        except FileNotFoundError:
            st.error("Could not find fleet_roster.csv / flight_bookings.csv "
                     "in the app folder. Export them from the R model, or "
                     "switch to 'Upload my own'.")
            st.stop()
    else:
        r_up = st.file_uploader("Fleet roster (CSV/Excel)",
                                type=["csv", "xlsx", "xls"])
        b_up = st.file_uploader("Flight bookings (CSV/Excel)",
                                type=["csv", "xlsx", "xls"])
        if r_up and b_up:
            roster = read_any(r_up)
            bookings = read_any(b_up)
            st.success(f"Loaded · {len(roster)} aircraft, {len(bookings):,} legs")
        else:
            st.info("Upload both files to continue.")
            st.stop()

    st.header("2 · Week")
    mode = st.radio("Select week by", ["Calendar date", "ISO week number"],
                    horizontal=True)

    if mode == "Calendar date":
        # Default to the median booking date so a populated week shows first
        try:
            _dates = pd.to_datetime(bookings.iloc[:, :].filter(
                regex="(?i)dep").iloc[:, 0])
            default_day = _dates.dropna().dt.date.median()
        except Exception:
            default_day = date(2027, 11, 24)
        picked = st.date_input("Any day in the target week", value=default_day)
        the_date = datetime.combine(picked, datetime.min.time())
        week = year = None
    else:
        col_a, col_b = st.columns(2)
        week = col_a.number_input("Week", min_value=1, max_value=53, value=47)
        year = col_b.number_input("Year", min_value=2022, max_value=2035,
                                  value=2027)
        the_date = None

    st.header("3 · Utilisation view")
    metric = st.selectbox(
        "Metric basis",
        options=["hours", "legs", "days"],
        format_func=lambda m: {
            "hours": "Aircraft commitment (% of week)",
            "legs": "Number of legs",
            "days": "Active days ÷ 7",
        }[m],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Resolve the week and run the analysis
# ─────────────────────────────────────────────────────────────────────────────
bsch = core.detect_booking_schema(bookings)
rsch = core.detect_roster_schema(roster)

required = ["tail", "dep_time", "arr_time", "dep_apt", "arr_apt"]
missing = [k for k in required if bsch[k] is None]
if missing:
    st.error(f"Couldn't detect these booking columns: {missing}")
    st.write("Columns found:", list(bookings.columns))
    st.stop()

if the_date is not None:
    wk_start, wk_end = core.week_bounds_from_date(the_date)
else:
    wk_start, wk_end = core.week_bounds_from_isoweek(int(year), int(week))

iso = wk_start.isocalendar()
wk_label = f"{wk_start:%d %b} – {(wk_end - timedelta(days=1)):%d %b %Y}  (Week {iso.week})"

bookings = bookings.copy()
bookings[bsch["dep_time"]] = core._to_naive(bookings[bsch["dep_time"]])
bookings[bsch["arr_time"]] = core._to_naive(bookings[bsch["arr_time"]])

week_legs = core.filter_week(bookings, bsch, wk_start, wk_end)
util_df = core.compute_utilisation(week_legs, roster, bsch, rsch,
                                   metric, wk_start, wk_end)
capacity_issues = core.check_capacity(week_legs, roster, bsch, rsch)


# ─────────────────────────────────────────────────────────────────────────────
# Top metrics row
# ─────────────────────────────────────────────────────────────────────────────
st.subheader(wk_label)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Flight legs", f"{len(week_legs):,}")
m2.metric("Active aircraft",
          f"{int((util_df['utilisation'] > 0).sum())} of {len(util_df)}")
m3.metric("Total hours flown",
          f"{util_df['flown_hrs'].sum():.0f}" if not week_legs.empty else "0")
issue_count = 0 if capacity_issues is None or capacity_issues.empty else len(capacity_issues)
m4.metric("Capacity warnings", issue_count,
          delta=None if issue_count == 0 else f"{issue_count} undersized",
          delta_color="inverse")

if capacity_issues is not None and not capacity_issues.empty:
    st.warning(
        f"{len(capacity_issues)} leg(s) have a travelling party larger than the "
        "assigned aircraft's seats. See the highlighted bars and table below."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Charts
# ─────────────────────────────────────────────────────────────────────────────
if week_legs.empty:
    st.info("No flights scheduled for this week. Try another week.")
else:
    tmpdir = Path(tempfile.mkdtemp())
    sched_path   = tmpdir / "sched.png"
    heatmap_path = tmpdir / "heatmap.png"
    bars_path    = tmpdir / "bars.png"

    core.schedule_chart(week_legs, roster, bsch, rsch,
                        wk_start, wk_end, wk_label, sched_path, capacity_issues)
    core.utilisation_heatmap(week_legs, roster, bsch, rsch,
                             wk_start, wk_end, wk_label, heatmap_path)
    core.utilisation_chart(util_df, metric, wk_start, wk_label, bars_path)

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Schedule", "Heatmap", "Utilisation", "Data"])

    with tab1:
        st.markdown("Aircraft schedule across the week, coloured by competition. "
                    "Capacity conflicts are flagged in red.")
        st.image(str(sched_path), use_container_width=True)
        with open(sched_path, "rb") as f:
            st.download_button("Download schedule chart (PNG)", f,
                               file_name=f"schedule_{iso.year}_W{iso.week:02d}.png",
                               mime="image/png")

    with tab2:
        st.markdown("Hours flown per aircraft per day. Every tail that flew this "
                    "week is shown, busiest at the top.")
        st.image(str(heatmap_path), use_container_width=True)
        with open(heatmap_path, "rb") as f:
            st.download_button("Download heatmap (PNG)", f,
                               file_name=f"heatmap_{iso.year}_W{iso.week:02d}.png",
                               mime="image/png")

    with tab3:
        st.markdown("Ranked utilisation by aircraft for the selected week.")
        st.image(str(bars_path), use_container_width=True)
        with open(bars_path, "rb") as f:
            st.download_button("Download utilisation chart (PNG)", f,
                               file_name=f"utilisation_{iso.year}_W{iso.week:02d}.png",
                               mime="image/png")

    with tab4:
        st.markdown("**Utilisation by aircraft**")
        st.dataframe(util_df, use_container_width=True, hide_index=True)
        if capacity_issues is not None and not capacity_issues.empty:
            st.markdown("**Capacity warnings**")
            show = capacity_issues[["tail_number", "seats", "party_size",
                                    "shortfall", "route"]]
            st.dataframe(show, use_container_width=True, hide_index=True)
        st.download_button(
            "Download utilisation table (CSV)",
            util_df.to_csv(index=False).encode(),
            file_name=f"utilisation_{iso.year}_W{iso.week:02d}.csv",
            mime="text/csv",
        )

st.markdown(f"<hr style='margin-top:2rem'>", unsafe_allow_html=True)
st.caption("Tetbury Aviation  ·  weekly fleet utilisation and schedule")
