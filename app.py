"""
Tetbury Aviation — Fleet Operations Console (Streamlit app)
===========================================================

A website-style console over the fleet utilisation tool: a navy masthead,
pill navigation between six windows (Overview / Schedule / Season /
Utilisation / Capacity / Data), custom KPI cards and white content panels.
The analytics layer is unchanged from the previous build.

RUN IT:
    streamlit run app.py

WINDOWS
    Overview     KPI grid, capacity alert, week-at-a-glance mini charts
    Schedule     interactive Gantt (hover, zoom, pan; conflicts hatched;
                 sub-charter overflow in its own lanes)
    Season       legs per week across the season, UEFA midweeks highlighted
    Utilisation  ranked commitment bars + aircraft-by-day heatmap
    Capacity     undersized legs with nearest-available-larger-aircraft swaps
    Data         tables and CSV downloads
"""
from __future__ import annotations
from datetime import datetime, date, timedelta
import warnings

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Silence Streamlit's plotly-config deprecation notice so it never renders
warnings.filterwarnings("ignore", message=".*keyword arguments have been deprecated.*")
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Reuse the exact same logic as the command-line tool — single source of truth
import tetbury_fleet_tool as core

# ─────────────────────────────────────────────────────────────────────────────
# Identity
# ─────────────────────────────────────────────────────────────────────────────
NAVY  = "#1F2A37"
INK   = "#141C26"   # deepest header tone
STEEL = "#3E5C76"
SKY   = "#748CAB"
AMBER = "#B07D48"
GREEN = "#5B8A72"
RED   = "#9E4244"
PANEL = "#F1F3F5"
GRID  = "#E2E6EA"
SLATE = "#6B7280"
BG    = "#F4F6F8"

DISPLAY = "Georgia, 'Times New Roman', serif"
UI      = "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"

CONFLICT = "⚠ Capacity conflict"
SUB_TAIL = "SUBCHARTER"

PAGES = ["Overview", "Schedule", "Season", "Utilisation", "Capacity", "Data"]

st.set_page_config(page_title="Tetbury Fleet Console", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown(f"""
<style>
/* ── base ─────────────────────────────────────────────────────────────── */
.stApp {{ background: {BG}; }}
html, body, .stMarkdown, .stText, p, div, span, label {{
    font-family: {UI};
}}
h1, h2, h3 {{ font-family: {DISPLAY}; color: {NAVY} !important; }}
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
#MainMenu, footer {{ visibility: hidden; }}
.block-container {{ padding-top: 0.6rem; padding-bottom: 3rem; max-width: 1500px; }}

/* ── masthead (contained band, not full-bleed, so the sidebar never clips it) ── */
.tet-mast {{
    width: 100%;
    background: linear-gradient(135deg, {INK} 0%, {NAVY} 55%, #2A3B4F 100%);
    border-bottom: 3px solid {AMBER};
    border-radius: 0 0 14px 14px;
    padding: 26px 2.5rem 22px;
    margin-bottom: 4px;
    display: flex; justify-content: space-between; align-items: flex-end;
    flex-wrap: wrap; gap: 12px;
    box-sizing: border-box;
}}
.tet-word {{
    font-family: {DISPLAY}; color: #FFFFFF; font-size: 1.85rem;
    font-weight: 700; letter-spacing: 0.16em; line-height: 1;
}}
.tet-word span {{ color: {SKY}; font-weight: 400; letter-spacing: 0.3em;
                  font-size: 0.95rem; display: block; margin-top: 6px; }}
.tet-week {{ text-align: right; color: #DDE4EB; font-family: {UI}; }}
.tet-week .lbl {{ font-size: 0.66rem; letter-spacing: 0.22em; color: {SKY};
                  text-transform: uppercase; }}
.tet-week .val {{ font-family: {DISPLAY}; font-size: 1.25rem; color: #FFFFFF;
                  margin-top: 2px; }}
.tet-week .sub {{ font-size: 0.78rem; color: #9FB0C2; margin-top: 3px; }}

/* ── pill navigation (styled radio) ───────────────────────────────────── */
.st-key-nav {{ margin: 18px 0 6px; }}
.st-key-nav [role="radiogroup"] {{
    display: inline-flex; gap: 2px; background: #FFFFFF;
    border: 1px solid {GRID}; border-radius: 12px; padding: 4px;
    box-shadow: 0 1px 3px rgba(31,42,55,0.06);
}}
.st-key-nav label[data-baseweb="radio"] {{
    padding: 8px 20px; border-radius: 9px; cursor: pointer;
    transition: background 0.15s; margin: 0 !important;
}}
.st-key-nav label[data-baseweb="radio"] > div:first-of-type {{ display: none; }}
.st-key-nav label[data-baseweb="radio"] p {{
    font-family: {UI}; font-size: 0.86rem; font-weight: 600;
    letter-spacing: 0.02em; color: #3A4453;
}}
.st-key-nav label[data-baseweb="radio"]:hover {{ background: {PANEL}; }}
.st-key-nav label[data-baseweb="radio"]:has(input:checked) {{
    background: {NAVY};
}}
.st-key-nav label[data-baseweb="radio"]:has(input:checked) p {{ color: #FFFFFF; }}

/* ── page header inside each window ───────────────────────────────────── */
.pg-eyebrow {{ font-size: 0.68rem; letter-spacing: 0.22em; text-transform: uppercase;
               color: {AMBER}; font-weight: 700; margin-bottom: 2px; }}
.pg-title {{ font-family: {DISPLAY}; font-size: 1.55rem; font-weight: 700;
             color: {NAVY}; line-height: 1.15; }}
.pg-desc {{ color: #4A5568; font-size: 0.9rem; max-width: 900px; margin-top: 4px; }}

/* ── KPI cards ────────────────────────────────────────────────────────── */
.kpi-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px;
             margin: 6px 0 4px; }}
@media (max-width: 1100px) {{ .kpi-grid {{ grid-template-columns: repeat(3, 1fr); }} }}
.kpi {{
    background: #FFFFFF; border: 1px solid {GRID}; border-radius: 12px;
    padding: 14px 16px 12px; position: relative; overflow: hidden;
    box-shadow: 0 1px 3px rgba(31,42,55,0.06);
}}
.kpi::before {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0;
                width: 3px; background: {STEEL}; }}
.kpi.warn::before {{ background: {RED}; }}
.kpi.gold::before {{ background: {AMBER}; }}
.kpi .k-lbl {{ font-size: 0.66rem; letter-spacing: 0.14em; text-transform: uppercase;
               color: #4A5568; font-weight: 700; }}
.kpi .k-val {{ font-family: {DISPLAY}; font-size: 1.7rem; color: {NAVY};
               line-height: 1.2; margin-top: 2px; }}
.kpi .k-val small {{ font-size: 0.9rem; color: {SLATE}; font-family: {UI}; }}
.kpi .k-chip {{ display: inline-block; margin-top: 6px; font-size: 0.66rem;
                font-weight: 700; letter-spacing: 0.04em; padding: 2px 8px;
                border-radius: 20px; }}
.k-chip.neutral {{ background: {PANEL}; color: {STEEL}; }}
.k-chip.gold {{ background: #F4EBDF; color: {AMBER}; }}
.k-chip.bad  {{ background: #F5E4E4; color: {RED}; }}
.k-chip.good {{ background: #E5EEE9; color: {GREEN}; }}

/* ── content panels (bordered containers become cards) ────────────────── */
div[data-testid="stVerticalBlockBorderWrapper"] {{
    background: #FFFFFF; border: 1px solid {GRID} !important;
    border-radius: 14px !important;
    box-shadow: 0 1px 3px rgba(31,42,55,0.06);
    padding: 6px 10px;
}}
.panel-h {{ font-family: {DISPLAY}; font-size: 1.02rem; font-weight: 700;
            color: {NAVY}; margin: 6px 4px 0; }}
.panel-s {{ color: #4A5568; font-size: 0.8rem; margin: 1px 4px 6px; }}

/* ── alert strip ──────────────────────────────────────────────────────── */
.alert-strip {{
    background: #FBF3F3; border: 1px solid #E8CFCF; border-left: 4px solid {RED};
    border-radius: 10px; padding: 12px 16px; margin: 10px 0 4px;
    color: {NAVY}; font-size: 0.88rem;
}}
.alert-strip b {{ color: {RED}; }}
.ok-strip {{
    background: #EFF5F1; border: 1px solid #D4E2D9; border-left: 4px solid {GREEN};
    border-radius: 10px; padding: 12px 16px; margin: 10px 0 4px;
    color: {NAVY}; font-size: 0.88rem;
}}

/* ── sidebar ──────────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {{ background: #FFFFFF; border-right: 1px solid {GRID}; }}
section[data-testid="stSidebar"] * {{ color: {NAVY}; }}
.sb-brand {{ font-family: {DISPLAY}; font-weight: 700; letter-spacing: 0.12em;
             color: {NAVY}; font-size: 1.0rem; padding: 4px 0 0; }}
.sb-eyebrow {{ font-size: 0.64rem; letter-spacing: 0.2em; text-transform: uppercase;
               color: {AMBER}; font-weight: 700; margin: 18px 0 2px;
               border-top: 1px solid {GRID}; padding-top: 14px; }}
span[data-baseweb="tag"] {{ background: {STEEL} !important; }}
span[data-baseweb="tag"] * {{ color: #FFFFFF !important; }}

/* expander as card */
div[data-testid="stExpander"] {{
    background: #FFFFFF; border: 1px solid {GRID}; border-radius: 12px;
}}
/* filter status line */
.filter-note {{ color: {SLATE}; font-size: 0.8rem; margin: 0 2px 8px; }}
.filter-note b {{ color: {STEEL}; }}

/* Streamlit alert / warning / deprecation boxes: force dark readable text
   (default can render white-on-yellow, which is unreadable) */
div[data-testid="stAlert"],
div[data-testid="stAlert"] *,
div[data-testid="stNotification"],
div[data-testid="stNotification"] *,
div[data-baseweb="notification"],
div[data-baseweb="notification"] * {{
    color: {INK} !important;
}}
div[data-testid="stAlert"] a,
div[data-baseweb="notification"] a {{ color: {STEEL} !important; }}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Airport city names (for hover tooltips). Optional dependency — degrades to
# IATA codes only if `airportsdata` isn't installed.
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def airport_cities() -> dict:
    # A few airports register a tiny municipality rather than the city they
    # serve — override those so hover labels read naturally.
    overrides = {
        "EAP": "Basel/Mulhouse", "OVD": "Asturias (Oviedo)",
        "BGY": "Milan-Bergamo", "NHT": "London (RAF Northolt)",
        "DSA": "Doncaster Sheffield", "LMA": "Malmö",
    }
    try:
        import airportsdata
        db = airportsdata.load("IATA")
        out = {k: v.get("city") or v.get("name", k) for k, v in db.items()}
        out.update(overrides)
        return out
    except Exception:
        return overrides


CITY = airport_cities()


def city_of(iata: str) -> str:
    return CITY.get(str(iata), str(iata))


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def load_from_disk():
    roster = pd.read_csv("fleet_roster.csv")
    bookings = core.clean_team_names(pd.read_csv("flight_bookings.csv"))
    return roster, bookings


def read_any(uploaded):
    if uploaded.name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded)
    return pd.read_csv(uploaded)


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — control panel
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="sb-brand">TETBURY · CONTROLS</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="sb-eyebrow">01 · Data source</div>',
                unsafe_allow_html=True)
    source = st.radio(
        "Fleet & bookings data",
        ["Project data files", "Upload my own"],
        label_visibility="collapsed",
        help="Project files are fleet_roster.csv and flight_bookings.csv "
             "in the app folder (exported from the R model).",
    )

    if source == "Project data files":
        try:
            roster, bookings = load_from_disk()
            st.caption(f"Loaded · {len(roster)} aircraft · "
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
            bookings = core.clean_team_names(read_any(b_up))
            st.caption(f"Loaded · {len(roster)} aircraft · {len(bookings):,} legs")
        else:
            st.info("Upload both files to continue.")
            st.stop()

    st.markdown('<div class="sb-eyebrow">02 · Operating week</div>',
                unsafe_allow_html=True)
    mode = st.radio("Select week by", ["Calendar date", "ISO week number"],
                    horizontal=True, label_visibility="collapsed")

    if mode == "Calendar date":
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


# ─────────────────────────────────────────────────────────────────────────────
# Resolve the week
# ─────────────────────────────────────────────────────────────────────────────
bsch = core.detect_booking_schema(bookings)
rsch = core.detect_roster_schema(roster)

required = ["tail", "dep_time", "arr_time", "dep_apt", "arr_apt"]
missing = [k for k in required if bsch[k] is None]
if missing:
    st.error(f"Couldn't detect these booking columns: {missing}")
    st.write("Columns found:", list(bookings.columns))
    st.stop()

CLUB_COL = "club" if "club" in bookings.columns else bsch["label"]

if the_date is not None:
    wk_start, wk_end = core.week_bounds_from_date(the_date)
else:
    wk_start, wk_end = core.week_bounds_from_isoweek(int(year), int(week))

iso = wk_start.isocalendar()
wk_label = f"{wk_start:%d %b} – {(wk_end - timedelta(days=1)):%d %b %Y}"

bookings = bookings.copy()
bookings[bsch["dep_time"]] = core._to_naive(bookings[bsch["dep_time"]])
bookings[bsch["arr_time"]] = core._to_naive(bookings[bsch["arr_time"]])
# Phase-boundary columns need the same datetime parsing, or the 3-phase Gantt
# compares strings to timestamps and errors.
for _pc in ("ground_start", "return_start"):
    if bsch.get(_pc) is not None:
        bookings[bsch[_pc]] = core._to_naive(bookings[bsch[_pc]])

week_legs = core.filter_week(bookings, bsch, wk_start, wk_end)


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — filters + view options
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="sb-eyebrow">03 · Filters</div>',
                unsafe_allow_html=True)
    comp_opts = sorted(bookings[bsch["comp"]].dropna().unique()) if bsch["comp"] else []
    sel_comps = st.multiselect("Competition / league", comp_opts,
                               placeholder="All competitions")

    _club_pool = bookings
    if sel_comps and bsch["comp"]:
        _club_pool = bookings[bookings[bsch["comp"]].isin(sel_comps)]
    club_opts = sorted(_club_pool[CLUB_COL].dropna().unique()) if CLUB_COL else []
    sel_clubs = st.multiselect("Club / national team", club_opts,
                               placeholder="All clubs",
                               help="Type to search. Filters every window — "
                                    "e.g. show one club its own legs, hours "
                                    "and timings.")

    st.markdown('<div class="sb-eyebrow">04 · Utilisation basis</div>',
                unsafe_allow_html=True)
    metric = st.selectbox(
        "Metric basis",
        options=["hours", "legs", "days"],
        label_visibility="collapsed",
        format_func=lambda m: {
            "hours": "Aircraft commitment (% of week)",
            "legs": "Number of legs",
            "days": "Active days ÷ 7",
        }[m],
    )


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    out = df
    if sel_comps and bsch["comp"]:
        out = out[out[bsch["comp"]].isin(sel_comps)]
    if sel_clubs and CLUB_COL:
        out = out[out[CLUB_COL].isin(sel_clubs)]
    return out


view_legs = apply_filters(week_legs)
filters_on = bool(sel_comps or sel_clubs)

core_legs = view_legs[view_legs[bsch["tail"]] != SUB_TAIL]
sub_legs = view_legs[view_legs[bsch["tail"]] == SUB_TAIL]

util_df = core.compute_utilisation(core_legs, roster, bsch, rsch,
                                   metric, wk_start, wk_end)


# ─────────────────────────────────────────────────────────────────────────────
# Capacity check + "nearest available larger aircraft" suggestions
# ─────────────────────────────────────────────────────────────────────────────
def capacity_table(legs: pd.DataFrame) -> pd.DataFrame:
    """Legs where the travelling party exceeds the assigned aircraft's seats."""
    if bsch["party"] is None:
        return pd.DataFrame()
    seats_of = dict(zip(roster[rsch["tail"]], roster[rsch["seats"]]))
    rows = []
    for _, leg in legs.iterrows():
        tail = leg[bsch["tail"]]
        seats = seats_of.get(tail)
        party = leg[bsch["party"]]
        if seats is not None and pd.notna(party) and party > seats:
            rows.append({
                "tail_number": tail,
                "seats": int(seats),
                "party_size": int(party),
                "shortfall": int(party - seats),
                "club": leg[CLUB_COL] if CLUB_COL else "",
                "competition": leg[bsch["comp"]] if bsch["comp"] else "",
                "route": f"{leg[bsch['dep_apt']]}→{leg[bsch['arr_apt']]}",
                "dep": leg[bsch["dep_time"]],
                "arr": leg[bsch["arr_time"]],
            })
    return pd.DataFrame(rows)


def add_suggestions(issues: pd.DataFrame, all_week_legs: pd.DataFrame,
                    buf_hrs: float = 1.5) -> pd.DataFrame:
    """For each undersized leg, find the smallest larger aircraft that is
    genuinely free across the leg's window (+turnaround buffer) that week.

    Availability is judged against the FULL week schedule (all clubs), not the
    filtered view, and suggestions are allocated sequentially -- largest
    shortfall first -- reserving each suggested tail for its window so the
    same aircraft is never offered to two overlapping conflicts.
    """
    if issues.empty:
        return issues
    seats_of = dict(zip(roster[rsch["tail"]], roster[rsch["seats"]]))
    type_of = (dict(zip(roster[rsch["tail"]], roster[rsch["type"]]))
               if rsch["type"] else {})
    buf = pd.Timedelta(hours=buf_hrs)
    tail_c, dep_c, arr_c = bsch["tail"], bsch["dep_time"], bsch["arr_time"]
    reserved: list[tuple] = []  # (tail, start, end) already promised

    def is_free(tail, start, end):
        sub = all_week_legs[all_week_legs[tail_c] == tail]
        for _, r in sub.iterrows():
            if (r[dep_c] - buf) < end and (r[arr_c] + buf) > start:
                return False
        for t, s, e in reserved:
            if t == tail and (s - buf) < end and (e + buf) > start:
                return False
        return True

    suggestions = {}
    for idx, row in issues.sort_values("shortfall", ascending=False).iterrows():
        cands = [t for t, s in seats_of.items()
                 if s >= row["party_size"] and t != row["tail_number"]
                 and is_free(t, row["dep"], row["arr"])]
        if cands:
            best = min(cands, key=lambda t: seats_of[t])
            reserved.append((best, row["dep"], row["arr"]))
            label = f"{best} · {int(seats_of[best])} seats"
            if type_of.get(best):
                label += f" · {type_of[best]}"
            suggestions[idx] = label
        else:
            suggestions[idx] = "None free that week — sub-charter"
    out = issues.copy()
    out["suggested_swap"] = out.index.map(suggestions)
    return out


capacity_issues = capacity_table(view_legs)
capacity_issues = add_suggestions(capacity_issues, week_legs)

n_fleet = len(roster)
active = int((util_df["utilisation"] > 0).sum()) if not util_df.empty else 0
core_hrs = float(util_df["flown_hrs"].sum()) if not util_df.empty else 0.0
commitment = 100.0 * core_hrs / (n_fleet * core.HOURS_PER_WEEK) if n_fleet else 0.0
issue_count = 0 if capacity_issues.empty else len(capacity_issues)


# ─────────────────────────────────────────────────────────────────────────────
# Interactive Gantt
# ─────────────────────────────────────────────────────────────────────────────
def pack_subcharter_lanes(legs: pd.DataFrame) -> pd.Series:
    """Assign overlapping sub-charter legs to separate lanes (greedy interval
    packing) so they no longer pile up on one unreadable row."""
    lane_free_at, lane_ids = [], {}
    for idx, leg in legs.sort_values(bsch["dep_time"]).iterrows():
        placed = False
        for i, free_at in enumerate(lane_free_at):
            if leg[bsch["dep_time"]] >= free_at:
                lane_free_at[i] = leg[bsch["arr_time"]]
                lane_ids[idx] = i
                placed = True
                break
        if not placed:
            lane_ids[idx] = len(lane_free_at)
            lane_free_at.append(leg[bsch["arr_time"]])
    return pd.Series(lane_ids)


def build_gantt_df(legs: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    seats_of = dict(zip(roster[rsch["tail"]], roster[rsch["seats"]]))
    type_of = (dict(zip(roster[rsch["tail"]], roster[rsch["type"]]))
               if rsch["type"] else {})
    flagged = (set(zip(capacity_issues["tail_number"], capacity_issues["dep"]))
               if not capacity_issues.empty else set())

    # Phase columns present? -> draw flight-out / gameday / flight-back segments
    has_phases = (bsch.get("ground_start") is not None
                  and bsch.get("return_start") is not None)

    core_part = legs[legs[bsch["tail"]] != SUB_TAIL]
    sub_part = legs[legs[bsch["tail"]] == SUB_TAIL].copy()

    rows = []
    for _, leg in core_part.iterrows():
        rows.append((leg, str(leg[bsch["tail"]])))
    if not sub_part.empty:
        lanes = pack_subcharter_lanes(sub_part)
        n = int(lanes.max()) + 1
        for idx, leg in sub_part.iterrows():
            lane = (f"Sub-charter #{lanes[idx] + 1:02d}" if n > 1
                    else "Sub-charter")
            rows.append((leg, lane))

    recs = []
    for leg, lane in rows:
        tail = leg[bsch["tail"]]
        seats = seats_of.get(tail)
        pax = leg[bsch["party"]] if bsch["party"] else None
        comp = leg[bsch["comp"]] if bsch["comp"] else "Other"
        club = leg[CLUB_COL] if CLUB_COL else ""
        route = f"{leg[bsch['dep_apt']]}→{leg[bsch['arr_apt']]}"
        citypair = (f"{city_of(leg[bsch['dep_apt']])} → "
                    f"{city_of(leg[bsch['arr_apt']])}")
        paxseats = (f"{int(pax)} pax / {int(seats)} seats"
                    if pd.notna(pax) and seats is not None
                    else (f"{int(pax)} pax / ad-hoc aircraft"
                          if pd.notna(pax) else "—"))
        aircraft = (f"{tail} · {type_of.get(tail, '')}".rstrip(" ·")
                    if tail != SUB_TAIL else "Sub-charter (spot market)")
        status = (CONFLICT if (tail, leg[bsch["dep_time"]]) in flagged else "OK")

        base = {
            "Lane": lane, "Competition": comp, "Club": club,
            "Route": route, "CityPair": citypair, "PaxSeats": paxseats,
            "Aircraft": aircraft, "Status": status,
        }

        if has_phases and pd.notna(leg[bsch["ground_start"]]) \
                and pd.notna(leg[bsch["return_start"]]):
            dep = leg[bsch["dep_time"]]
            g0  = leg[bsch["ground_start"]]
            r0  = leg[bsch["return_start"]]
            end = leg[bsch["arr_time"]]
            segments = [
                ("Flight out", dep, g0, route),
                ("Gameday",    g0,  r0,  club or "on ground"),
                ("Flight back", r0, end, f"{leg[bsch['arr_apt']]}→"
                                          f"{leg[bsch['dep_apt']]}"),
            ]
            for phase, s, f, lbl in segments:
                if pd.isna(s) or pd.isna(f) or f <= s:
                    continue
                recs.append({**base, "Phase": phase, "Start": s, "Finish": f,
                             "SegLabel": lbl,
                             "Times": f"{s:%a %d %b %H:%M} → {f:%a %d %b %H:%M}"})
        else:
            recs.append({**base, "Phase": "Occupation",
                         "Start": leg[bsch["dep_time"]],
                         "Finish": leg[bsch["arr_time"]],
                         "SegLabel": route,
                         "Times": f"{leg[bsch['dep_time']]:%a %d %b %H:%M} → "
                                  f"{leg[bsch['arr_time']]:%a %d %b %H:%M}"})

    df = pd.DataFrame(recs)

    # Lane order: core tails by first departure, sub-charter lanes at the bottom
    firsts = df.groupby("Lane")["Start"].min().sort_values()
    core_order = [l for l in firsts.index if not l.startswith("Sub-charter")]
    sub_order = sorted(l for l in firsts.index if l.startswith("Sub-charter"))
    return df, core_order + sub_order


def gantt_figure(df: pd.DataFrame, order: list[str]) -> go.Figure:
    color_map = {c: core.COMP_COLORS.get(c, core.DEFAULT_COLOR)
                 for c in df["Competition"].unique()}
    has_phases = "Phase" in df.columns and set(df["Phase"].unique()) != {"Occupation"}

    fig = go.Figure()
    lane_index = {lane: i for i, lane in enumerate(order[::-1])}

    # Draw each segment as a horizontal bar via Scatter so we control phase shading
    seen_comps = set()
    for _, r in df.iterrows():
        y = lane_index.get(r["Lane"])
        if y is None:
            continue
        comp = r["Competition"]
        base_col = color_map.get(comp, core.DEFAULT_COLOR)
        phase = r.get("Phase", "Occupation")
        conflict = r["Status"] == CONFLICT

        # Phase styling: flights solid full-height; gameday lighter & shorter
        if phase == "Gameday":
            fill = _tint(base_col, 0.55)   # lighter tint for on-ground time
            hh = 0.22
        elif phase in ("Flight out", "Flight back"):
            fill = base_col                 # solid for actual flying
            hh = 0.34
        else:  # single-bar fallback
            fill = base_col
            hh = 0.34

        x0, x1 = r["Start"], r["Finish"]
        hover = (f"<b>{r['Club']}</b> — {comp}<br>"
                 f"{r['CityPair']}<br>{r['PaxSeats']}<br>"
                 f"{r['Aircraft']}<br>{phase}: {r['Times']}")
        # Filled polygon for the visible bar (hover only fires on its edge)
        fig.add_trace(go.Scatter(
            x=[x0, x1, x1, x0, x0],
            y=[y-hh, y-hh, y+hh, y+hh, y-hh],
            fill="toself", mode="lines",
            fillcolor=fill,
            line=dict(color="#7B1010" if conflict else "white",
                      width=2.2 if conflict else 0.5),
            hoverinfo="skip",
            showlegend=False,
        ))
        # Invisible dense marker line across the bar so hover works everywhere
        mid_pts = pd.date_range(x0, x1, periods=12)
        fig.add_trace(go.Scatter(
            x=list(mid_pts), y=[y] * len(mid_pts),
            mode="markers",
            marker=dict(size=14, color="rgba(0,0,0,0)"),
            hoverinfo="text",
            hovertext=[hover] * len(mid_pts),
            showlegend=False,
        ))

    # Competition legend (proxy traces)
    for comp in sorted(color_map):
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=10, color=color_map[comp], symbol="square"),
            name=comp, showlegend=True,
        ))
    # Phase legend hint
    if has_phases:
        for lbl, shade in [("Flight (out/back)", 1.0), ("Gameday on ground", 0.55)]:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color=_tint(SLATE, shade), symbol="square"),
                name=lbl, showlegend=True,
            ))

    fig.update_yaxes(
        tickmode="array", tickvals=list(range(len(order))),
        ticktext=order[::-1], title=None,
        tickfont=dict(size=11, family=UI, color=NAVY), range=[-0.6, len(order)-0.4],
    )
    fig.update_xaxes(
        range=[wk_start, wk_end], dtick=86400000, tickformat="%a %d %b",
        gridcolor="#E8EDF2", title=None,
        tickfont=dict(size=11, family=UI, color=NAVY),
    )
    n_sub = sum(1 for l in order if l.startswith("Sub-charter"))
    if n_sub:
        fig.add_hrect(y0=-0.5, y1=n_sub - 0.5, fillcolor=RED, opacity=0.05,
                      line_width=0)
    fig.update_layout(
        height=max(420, 26 * len(order) + 190),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family=UI, color=NAVY),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0,
                    font=dict(size=11, color=NAVY)),
        margin=dict(l=10, r=10, t=10, b=10),
        hoverlabel=dict(font=dict(family=UI, size=12)),
    )
    return fig


def _tint(hex_color: str, factor: float) -> str:
    """Lighten a hex colour toward white by `1-factor` (factor=1 -> unchanged)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = int(r + (255 - r) * (1 - factor))
    g = int(g + (255 - g) * (1 - factor))
    b = int(b + (255 - b) * (1 - factor))
    return f"#{r:02x}{g:02x}{b:02x}"


# ─────────────────────────────────────────────────────────────────────────────
# Season view
# ─────────────────────────────────────────────────────────────────────────────
COMP_GROUPS = [
    ("UEFA club (midweek)", lambda c: c in {
        "UEFA Champions League", "UEFA Europa League",
        "UEFA Conference League", "UEFA Women's Champions League"}),
    ("UEFA qualifying", lambda c: "Qualifying" in c),
    ("Domestic league", lambda c: c in {
        "Premier League", "Bundesliga", "Serie A", "La Liga",
        "Ligue 1", "Primeira Liga"}),
]
GROUP_COLORS = {
    "UEFA club (midweek)": STEEL,
    "UEFA qualifying": AMBER,
    "Domestic league": SKY,
    "National team / other": "#B9C2CC",
}


def comp_group(c: str) -> str:
    for name, test in COMP_GROUPS:
        if test(str(c)):
            return name
    return "National team / other"


def season_bounds(d: datetime) -> tuple[datetime, datetime, str]:
    y = d.year if d.month >= 7 else d.year - 1
    return (datetime(y, 7, 1), datetime(y + 1, 7, 1), f"{y}/{str(y+1)[-2:]}")


def season_figure() -> go.Figure | None:
    s_start, s_end, s_label = season_bounds(wk_start)
    dfp = apply_filters(bookings)
    dfp = dfp[(dfp[bsch["dep_time"]] >= s_start) &
              (dfp[bsch["dep_time"]] < s_end)].copy()
    if dfp.empty:
        return None
    dfp["week_start"] = (dfp[bsch["dep_time"]]
                         - pd.to_timedelta(dfp[bsch["dep_time"]].dt.weekday,
                                           unit="D")).dt.normalize()
    dfp["group"] = dfp[bsch["comp"]].map(comp_group) if bsch["comp"] else "Other"

    weekly = (dfp.groupby(["week_start", "group"]).size()
                 .rename("legs").reset_index())
    sub_weekly = (dfp[dfp[bsch["tail"]] == SUB_TAIL]
                  .groupby("week_start").size().rename("sub_legs"))

    fig = px.bar(
        weekly, x="week_start", y="legs", color="group",
        color_discrete_map=GROUP_COLORS,
        category_orders={"group": list(GROUP_COLORS)},
    )
    fig.update_traces(marker_line_width=0,
                      hovertemplate="Week of %{x|%d %b %Y}<br>"
                                    "%{fullData.name}: %{y} legs<extra></extra>")

    # Sub-charter weeks flagged above the stack
    if not sub_weekly.empty:
        totals = weekly.groupby("week_start")["legs"].sum()
        xs = sub_weekly.index
        fig.add_trace(go.Scatter(
            x=xs, y=[totals.get(x, 0) + 3 for x in xs],
            mode="markers", name="Sub-charter used",
            marker=dict(symbol="triangle-down", size=9, color=RED),
            customdata=sub_weekly.values,
            hovertemplate="Week of %{x|%d %b %Y}<br>"
                          "%{customdata} sub-charter leg(s)<extra></extra>",
        ))

    # Mark the selected week
    fig.add_vrect(x0=wk_start, x1=wk_end, fillcolor=STEEL, opacity=0.12,
                  line_width=0)
    fig.add_annotation(x=wk_start + timedelta(days=3.5), yref="paper", y=1.04,
                       text=f"Week {iso.week}", showarrow=False,
                       font=dict(size=11, color=STEEL, family=UI))

    fig.update_xaxes(title=None, gridcolor="#F0F3F6", tickformat="%b %Y",
                     tickfont=dict(color=NAVY))
    fig.update_yaxes(title="Flight legs per week", gridcolor="#F0F3F6",
                     tickfont=dict(color=NAVY),
                     title_font=dict(color=NAVY))
    fig.update_layout(
        barmode="stack", height=440,
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family=UI, color=NAVY),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0,
                    font=dict(size=11, color=NAVY)),
        margin=dict(l=10, r=10, t=48, b=10),
        hoverlabel=dict(font=dict(family=UI, size=12)),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Utilisation charts
# ─────────────────────────────────────────────────────────────────────────────
def utilisation_figure() -> go.Figure | None:
    act = util_df[util_df["utilisation"] > 0]
    if act.empty:
        return None
    med = act["utilisation"].median()
    colors = [STEEL if v >= med else SKY for v in act["utilisation"]]
    suffix = "" if metric == "legs" else "%"
    fig = go.Figure(go.Bar(
        x=act["utilisation"], y=act["tail_number"], orientation="h",
        marker_color=colors,
        text=[f"{v}{suffix}  ({h}h, {n} legs)"
              for v, h, n in zip(act["utilisation"], act["flown_hrs"],
                                 act["n_legs"])],
        textposition="outside",
        textfont=dict(size=11, family=UI, color=NAVY),
        customdata=np.stack([act["flown_hrs"], act["n_legs"],
                             act["active_days"]], axis=-1),
        hovertemplate="<b>%{y}</b><br>Utilisation: %{x}" + suffix +
                      "<br>%{customdata[0]} h committed · %{customdata[1]} legs"
                      "<br>Active on %{customdata[2]} day(s)<extra></extra>",
    ))
    metric_lbl = {"hours": "Aircraft commitment (% of 168-hr week)",
                  "legs": "Number of legs flown",
                  "days": "Active days (% of 7)"}[metric]
    fig.update_layout(
        height=max(380, 26 * len(act) + 120),
        xaxis=dict(title=metric_lbl, gridcolor="#F0F3F6",
                   tickfont=dict(color=NAVY), title_font=dict(color=NAVY),
                   range=[0, act["utilisation"].max() * 1.35]),
        yaxis=dict(autorange="reversed", title=None,
                   tickfont=dict(color=NAVY, size=12)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family=UI, color=NAVY),
        margin=dict(l=10, r=10, t=10, b=10),
        hoverlabel=dict(font=dict(family=UI, size=12)),
    )
    return fig


def heatmap_figure() -> go.Figure | None:
    legs = core_legs.copy()
    if legs.empty:
        return None
    legs["_hours"] = (legs[bsch["arr_time"]] - legs[bsch["dep_time"]]
                      ).dt.total_seconds() / 3600.0
    legs["_day"] = (legs[bsch["dep_time"]].dt.normalize()
                    - pd.Timestamp(wk_start)).dt.days
    legs = legs[(legs["_day"] >= 0) & (legs["_day"] < 7)]
    order = (legs.groupby(bsch["tail"])["_hours"].sum()
             .sort_values(ascending=False).index.tolist())
    grid = np.zeros((len(order), 7))
    for _, leg in legs.iterrows():
        grid[order.index(leg[bsch["tail"]]), int(leg["_day"])] += leg["_hours"]

    days = [(wk_start + timedelta(days=d)).strftime("%a %d %b") for d in range(7)]
    fig = go.Figure(go.Heatmap(
        z=grid, x=days, y=order,
        colorscale=[[0, "#F1F3F5"], [0.35, "#AEBCCC"], [0.6, SKY],
                    [0.85, STEEL], [1, NAVY]],
        xgap=2, ygap=2,
        text=np.where(grid > 0, np.round(grid, 1).astype(str), ""),
        texttemplate="%{text}",
        textfont=dict(size=10, family=UI),
        colorbar=dict(title="Hours", tickfont=dict(family=UI, color=NAVY)),
        hovertemplate="<b>%{y}</b> · %{x}<br>%{z:.1f} h committed<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=11, family=UI, color=NAVY))
    fig.update_layout(
        height=max(380, 24 * len(order) + 140),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family=UI, color=NAVY),
        margin=dict(l=10, r=10, t=10, b=10),
        hoverlabel=dict(font=dict(family=UI, size=12)),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# UI building blocks
# ─────────────────────────────────────────────────────────────────────────────
def masthead():
    sub = f"{len(view_legs):,} legs · {active} of {n_fleet} aircraft active"
    if filters_on:
        sub += " · filtered view"
    st.markdown(f"""
    <div class="tet-mast">
      <div class="tet-word">TETBURY<span>AVIATION · FLEET OPERATIONS CONSOLE</span></div>
      <div class="tet-week">
        <div class="lbl">Operating week {iso.week} · {iso.year}</div>
        <div class="val">{wk_label}</div>
        <div class="sub">{sub}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


def page_header(eyebrow: str, title: str, desc: str):
    st.markdown(f"""
    <div style="margin: 10px 0 14px;">
      <div class="pg-eyebrow">{eyebrow}</div>
      <div class="pg-title">{title}</div>
      <div class="pg-desc">{desc}</div>
    </div>
    """, unsafe_allow_html=True)


def filter_note():
    if not filters_on:
        return
    bits = []
    if sel_comps:
        bits.append("<b>" + (", ".join(sel_comps) if len(sel_comps) <= 3
                             else f"{len(sel_comps)} competitions") + "</b>")
    if sel_clubs:
        bits.append("<b>" + (", ".join(sel_clubs) if len(sel_clubs) <= 3
                             else f"{len(sel_clubs)} clubs") + "</b>")
    st.markdown(f'<div class="filter-note">Filtered to {" · ".join(bits)} — '
                f'all figures, charts and tables reflect this view.</div>',
                unsafe_allow_html=True)


def kpi_card(label, value, chip=None, tone="neutral", accent=""):
    chip_html = (f'<span class="k-chip {tone}">{chip}</span>' if chip else "")
    return (f'<div class="kpi {accent}"><div class="k-lbl">{label}</div>'
            f'<div class="k-val">{value}</div>{chip_html}</div>')


def kpi_grid():
    cards = [
        kpi_card("Flight legs", f"{len(view_legs):,}"),
        kpi_card("Active aircraft", f"{active} <small>of {n_fleet}</small>",
                 chip="Fleet saturated" if active == n_fleet and n_fleet else None,
                 tone="gold", accent="gold" if active == n_fleet else ""),
        kpi_card("Sub-charter legs", f"{len(sub_legs):,}",
                 chip=None if sub_legs.empty else "Overflow on spot market",
                 tone="bad", accent="warn" if len(sub_legs) else ""),
        kpi_card("Hours committed", f"{core_hrs:,.0f}<small> h</small>"),
        kpi_card("Fleet commitment", f"{commitment:.1f}%"),
        kpi_card("Capacity warnings", f"{issue_count}",
                 chip="All parties seated" if issue_count == 0
                      else f"{issue_count} undersized",
                 tone="good" if issue_count == 0 else "bad",
                 accent="warn" if issue_count else ""),
    ]
    st.markdown('<div class="kpi-grid">' + "".join(cards) + "</div>",
                unsafe_allow_html=True)


def capacity_strip():
    if issue_count:
        st.markdown(f"""
        <div class="alert-strip"><b>{issue_count} undersized leg(s)</b> — a
        travelling party larger than the assigned aircraft's seats. The
        <b>Capacity</b> window lists each leg with the nearest available larger
        aircraft — exactly what a centralised allocator resolves automatically.
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown('<div class="ok-strip">Every travelling party is seated — '
                    'no capacity conflicts in this view.</div>',
                    unsafe_allow_html=True)


def capacity_actions_table():
    show = capacity_issues.assign(
        leg=capacity_issues["dep"].dt.strftime("%a %d %b %H:%M"),
        pax_vs_seats=capacity_issues.apply(
            lambda r: f"{r['party_size']} pax / {r['seats']} seats "
                      f"(short {r['shortfall']})", axis=1),
    )[["leg", "club", "competition", "route", "tail_number",
       "pax_vs_seats", "suggested_swap"]]
    show.columns = ["Departure", "Club", "Competition", "Route",
                    "Assigned tail", "Party vs seats",
                    "Nearest available larger aircraft"]
    st.dataframe(show, width="stretch", hide_index=True)
    st.download_button(
        "Download reassignment list (CSV)",
        show.to_csv(index=False).encode(),
        file_name=f"capacity_actions_{iso.year}_W{iso.week:02d}.csv",
        mime="text/csv",
    )


PLOTLY_CFG = {"displaylogo": False}


# ─────────────────────────────────────────────────────────────────────────────
# Windows
# ─────────────────────────────────────────────────────────────────────────────
def page_overview():
    page_header("Window 01", "Week at a glance",
                "The operating position for the selected week — demand, fleet "
                "saturation, overflow bought on the spot market, and capacity "
                "conflicts. Every figure respects the sidebar filters.")
    kpi_grid()
    capacity_strip()

    if view_legs.empty:
        return
    c1, c2 = st.columns([1, 1])
    with c1, st.container(border=True):
        st.markdown('<div class="panel-h">Legs by competition</div>'
                    '<div class="panel-s">Where this week\'s demand comes '
                    'from.</div>', unsafe_allow_html=True)
        comp_counts = (view_legs.groupby(bsch["comp"]).size()
                       .sort_values() if bsch["comp"] else pd.Series())
        fig = go.Figure(go.Bar(
            x=comp_counts.values, y=comp_counts.index, orientation="h",
            marker_color=[core.COMP_COLORS.get(c, core.DEFAULT_COLOR)
                          for c in comp_counts.index],
            hovertemplate="<b>%{y}</b>: %{x} legs<extra></extra>",
            text=comp_counts.values, textposition="outside",
            textfont=dict(family=UI, size=11, color=NAVY),
        ))
        fig.update_layout(
            height=max(240, 30 * len(comp_counts) + 60),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(family=UI, color=NAVY),
            xaxis=dict(gridcolor="#F0F3F6", tickfont=dict(color=NAVY),
                       range=[0, comp_counts.max() * 1.18] if len(comp_counts) else None),
            yaxis=dict(tickfont=dict(color=NAVY, size=12)),
            margin=dict(l=6, r=6, t=6, b=6),
        )
        st.plotly_chart(fig, config=PLOTLY_CFG)

    with c2, st.container(border=True):
        st.markdown('<div class="panel-h">Busiest aircraft</div>'
                    '<div class="panel-s">Top of the utilisation ranking — '
                    'the full list is in the Utilisation window.</div>',
                    unsafe_allow_html=True)
        top = util_df[util_df["utilisation"] > 0].head(8)
        suffix = "" if metric == "legs" else "%"
        fig = go.Figure(go.Bar(
            x=top["utilisation"][::-1], y=top["tail_number"][::-1],
            orientation="h", marker_color=STEEL,
            text=[f"{v}{suffix}" for v in top["utilisation"][::-1]],
            textposition="outside", textfont=dict(family=UI, size=11, color=NAVY),
            hovertemplate="<b>%{y}</b>: %{x}" + suffix + "<extra></extra>",
        ))
        fig.update_layout(
            height=max(240, 30 * len(top) + 60),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(family=UI, color=NAVY),
            xaxis=dict(gridcolor="#F0F3F6", tickfont=dict(color=NAVY),
                       range=[0, top["utilisation"].max() * 1.18] if len(top) else None),
            yaxis=dict(tickfont=dict(color=NAVY, size=12)),
            margin=dict(l=6, r=6, t=6, b=6),
        )
        st.plotly_chart(fig, config=PLOTLY_CFG)


def page_schedule():
    page_header("Window 02", "Weekly schedule",
                "One row per aircraft, coloured by competition. Hover any bar "
                "for club, city pair and pax vs seats; drag to zoom, "
                "double-click to reset. Hatched bars with a dark border are "
                "capacity conflicts; sub-charter overflow sits in its own "
                "lanes on the faint red band at the bottom.")
    if view_legs.empty:
        st.info("No flights match this week and filter selection.")
        return
    with st.container(border=True):
        gdf, order = build_gantt_df(view_legs)
        st.plotly_chart(gantt_figure(gdf, order), config=PLOTLY_CFG)


def page_season():
    page_header("Window 03", "Season pressure",
                "The same congestion, season-wide — evidence that the load is "
                "structural rather than a one-off week. Steel-blue segments "
                "are UEFA club midweeks; red markers are weeks where overflow "
                "was bought on the spot market; the shaded band is the "
                "selected week.")
    sfig = season_figure()
    if sfig is None:
        st.info("No legs in this season for the current filters.")
        return
    with st.container(border=True):
        st.plotly_chart(sfig, config=PLOTLY_CFG)


def page_utilisation():
    page_header("Window 04", "Fleet utilisation",
                "Ranked commitment by aircraft, and committed hours per "
                "aircraft per day. Commitment counts the full occupation "
                "window — day-before positioning through the return leg — "
                "which is why a single fixture ties an aircraft up for well "
                "over a day.")
    if view_legs.empty:
        st.info("No flights match this week and filter selection.")
        return
    ufig = utilisation_figure()
    if ufig is None:
        st.info("No active aircraft for this selection.")
        return
    with st.container(border=True):
        st.markdown('<div class="panel-h">Ranked utilisation</div>',
                    unsafe_allow_html=True)
        st.plotly_chart(ufig, config=PLOTLY_CFG)
    hfig = heatmap_figure()
    if hfig is not None:
        with st.container(border=True):
            st.markdown('<div class="panel-h">Hours by aircraft and day</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(hfig, config=PLOTLY_CFG)


def page_capacity():
    page_header("Window 05", "Capacity actions",
                "Legs where the travelling party exceeds the assigned "
                "aircraft's seats, with the nearest available larger aircraft "
                "that week. Suggestions check genuine availability across the "
                "leg's window plus turnaround, and never offer the same "
                "aircraft to two overlapping conflicts.")
    capacity_strip()
    if issue_count == 0:
        return
    with st.container(border=True):
        capacity_actions_table()
    st.caption("Legs marked \u201cNone free that week\u201d have no idle "
               "larger aircraft in the fleet for that window — the sub-charter "
               "case in one line.")


def page_data():
    page_header("Window 06", "Data & downloads",
                "The tables behind every chart, filtered to the current view, "
                "with CSV exports.")
    with st.container(border=True):
        st.markdown('<div class="panel-h">Utilisation by aircraft</div>',
                    unsafe_allow_html=True)
        st.dataframe(util_df, width="stretch", hide_index=True)

    legs_show = view_legs.copy()
    legs_show["city_pair"] = (legs_show[bsch["dep_apt"]].map(city_of)
                              + " → "
                              + legs_show[bsch["arr_apt"]].map(city_of))
    with st.container(border=True):
        st.markdown('<div class="panel-h">Flight legs (filtered view)</div>',
                    unsafe_allow_html=True)
        st.dataframe(legs_show, width="stretch", hide_index=True)

    if issue_count:
        with st.container(border=True):
            st.markdown('<div class="panel-h">Capacity warnings & suggested '
                        'reassignments</div>', unsafe_allow_html=True)
            st.dataframe(
                capacity_issues[["tail_number", "club", "seats", "party_size",
                                 "shortfall", "route", "suggested_swap"]],
                width="stretch", hide_index=True)

    c1, c2 = st.columns(2)
    c1.download_button(
        "Download utilisation table (CSV)",
        util_df.to_csv(index=False).encode(),
        file_name=f"utilisation_{iso.year}_W{iso.week:02d}.csv",
        mime="text/csv",
    )
    c2.download_button(
        "Download filtered legs (CSV)",
        legs_show.to_csv(index=False).encode(),
        file_name=f"legs_{iso.year}_W{iso.week:02d}.csv",
        mime="text/csv",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Layout
# ─────────────────────────────────────────────────────────────────────────────
masthead()

nav = st.radio("Navigation", PAGES, horizontal=True, key="nav",
               label_visibility="collapsed")
filter_note()

if view_legs.empty and nav in ("Overview", "Schedule", "Utilisation"):
    if nav == "Overview":
        page_header("Window 01", "Week at a glance", "")
        kpi_grid()
    st.info("No flights match this week and filter selection. "
            "Try another week or clear the filters.")
else:
    {"Overview": page_overview,
     "Schedule": page_schedule,
     "Season": page_season,
     "Utilisation": page_utilisation,
     "Capacity": page_capacity,
     "Data": page_data}[nav]()

st.markdown(f"""
<div style="margin-top:3rem; padding-top:1rem; border-top:1px solid {GRID};
            display:flex; justify-content:space-between; color:{SLATE};
            font-size:0.75rem;">
  <span>TETBURY AVIATION · European football charter fleet</span>
  <span>Weekly fleet utilisation and schedule · RVHP data model</span>
</div>
""", unsafe_allow_html=True)
