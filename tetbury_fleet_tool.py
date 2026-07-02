"""
Tetbury Aviation — Weekly Fleet Utilisation & Schedule Tool
===========================================================

Plans and visualises aircraft usage for chartered flights servicing European
football clubs, for a single selected week.

USAGE
-----
    # By ISO week number + year:
    python tetbury_fleet_tool.py --week 42 --year 2027

    # By a start date (any day in the target week; Monday is derived):
    python tetbury_fleet_tool.py --date 2027-10-18

    # Custom file paths / utilisation metric / output dir:
    python tetbury_fleet_tool.py --week 42 --year 2027 \
        --roster fleet_roster.csv --bookings flight_bookings.csv \
        --metric hours --outdir charts

The tool auto-detects column names (no fixed schema assumed). If detection fails
for a required field, it prints the available columns and asks you to map them
via the --map option.

OUTPUTS
-------
    <outdir>/utilisation_<year>_W<week>.png   bar chart, util% per aircraft
    <outdir>/schedule_<year>_W<week>.png      Gantt timeline, one row per tail
"""

from __future__ import annotations
import argparse
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch

# ─────────────────────────────────────────────────────────────────────────────
# Styling
# ─────────────────────────────────────────────────────────────────────────────
# Cool-grey corporate palette
NAVY    = "#1F2A37"   # deep slate (primary text / headers)
STEEL   = "#3E5C76"   # muted steel blue (primary accent)
SKY     = "#748CAB"   # soft blue-grey (secondary)
AMBER   = "#B07D48"   # muted bronze (warm accent)
GREEN   = "#5B8A72"   # sage (positive)
RED     = "#9E4244"   # brick (alert)
SLATE   = "#6B7280"   # neutral grey (labels)
LIGHT   = "#F1F3F5"   # cool off-white (panels)
GRID    = "#E2E6EA"   # gridlines

COMP_COLORS = {
    "UEFA Champions League": "#3E5C76",
    "UEFA Europa League":    "#B07D48",
    "UEFA Conference League":"#5B8A72",
    "Premier League":        "#6C5B7B",
    "La Liga":               "#9E4244",
    "Serie A":               "#4A7C7E",
    "Bundesliga":            "#A0623B",
    "Ligue 1":               "#566A8A",
    "Primeira Liga":         "#7A8290",
}
DEFAULT_COLOR = "#9AA5B1"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Liberation Serif", "Times New Roman"],
    "axes.edgecolor": SLATE,
    "axes.linewidth": 0.8,
    "axes.titlecolor": NAVY,
})


# ─────────────────────────────────────────────────────────────────────────────
# Schema detection — inspect actual columns instead of assuming
# ─────────────────────────────────────────────────────────────────────────────
def _find(cols, *candidates):
    """Return the first column whose lowercased name contains any candidate."""
    low = {c.lower(): c for c in cols}
    # exact-ish contains match
    for cand in candidates:
        for lc, orig in low.items():
            if cand in lc:
                return orig
    return None


def detect_booking_schema(df: pd.DataFrame) -> dict:
    cols = list(df.columns)
    schema = {
        "tail":     _find(cols, "tail", "aircraft", "registration", "reg"),
        "dep_time": _find(cols, "dep_datetime", "departure", "dep_date", "std", "off_block"),
        "arr_time": _find(cols, "arr_datetime", "arrival", "arr_date", "sta", "on_block"),
        "dep_apt":  _find(cols, "dep_airport", "origin", "from", "dep_iata"),
        "arr_apt":  _find(cols, "arr_airport", "dest", "to", "arr_iata"),
        "label":    _find(cols, "match_label", "club", "match", "team", "competition"),
        "comp":     _find(cols, "competition", "comp"),
        "party":    _find(cols, "party_size", "party", "pax", "passengers", "travelling"),
    }
    return schema


def detect_roster_schema(df: pd.DataFrame) -> dict:
    cols = list(df.columns)
    return {
        "tail":  _find(cols, "tail", "aircraft", "registration", "reg"),
        "type":  _find(cols, "type", "model"),
        "seats": _find(cols, "seat", "capacity", "pax"),
        "base":  _find(cols, "base", "home", "hub"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Week selection
# ─────────────────────────────────────────────────────────────────────────────
def week_bounds_from_isoweek(year: int, week: int) -> tuple[datetime, datetime]:
    # ISO: Monday is day 1
    monday = date.fromisocalendar(year, week, 1)
    start = datetime.combine(monday, datetime.min.time())
    return start, start + timedelta(days=7)


def week_bounds_from_date(d: datetime) -> tuple[datetime, datetime]:
    monday = d - timedelta(days=d.weekday())
    start = datetime.combine(monday.date(), datetime.min.time())
    return start, start + timedelta(days=7)


# ─────────────────────────────────────────────────────────────────────────────
# Core processing
# ─────────────────────────────────────────────────────────────────────────────
# Common club-name aliases -> canonical form. Extend as needed; keeps string
# filters foolproof for live demos (e.g. "Chelsea" vs "Chelsea FC").
CLUB_ALIASES = {
    "Chelsea FC": "Chelsea",
    "Paris Saint-Germain FC": "PSG",
    "Paris Saint-Germain": "PSG",
    "FC Bayern Munich": "Bayern Munich",
    "FC Bayern München": "Bayern Munich",
    "Bayern München": "Bayern Munich",
    "Inter": "Inter Milan",
    "Internazionale": "Inter Milan",
    "FC Internazionale Milano": "Inter Milan",
    "LOSC Lille": "Lille",
    "AFC Ajax": "Ajax",
    "Sporting Clube de Portugal": "Sporting CP",
    "Sporting Lisbon": "Sporting CP",
}


def clean_team_names(df: pd.DataFrame, cols=None) -> pd.DataFrame:
    """Strip trailing whitespace and map known aliases to canonical club names.

    Runs before any filtering so club-level filters are reliable. Applies to the
    given columns, or auto-detects likely team/club columns if none supplied.
    """
    out = df.copy()
    if cols is None:
        cols = [c for c in out.columns
                if any(k in c.lower() for k in ("club", "team", "home", "away"))
                and out[c].dtype == object]
    for c in cols:
        out[c] = (out[c].astype(str)
                        .str.strip()
                        .replace(CLUB_ALIASES))
    return out


def load_data(roster_path: str, bookings_path: str):
    def _read(p):
        p = Path(p)
        if p.suffix.lower() in (".xlsx", ".xls"):
            return pd.read_excel(p)
        return pd.read_csv(p)
    roster = _read(roster_path)
    bookings = clean_team_names(_read(bookings_path))
    return roster, bookings


def _to_naive(s: pd.Series) -> pd.Series:
    """Parse to datetime and drop any timezone so comparisons stay consistent."""
    out = pd.to_datetime(s, errors="coerce")
    if hasattr(out.dtype, "tz") and out.dtype.tz is not None:
        out = out.dt.tz_localize(None)
    return out


def filter_week(bookings: pd.DataFrame, schema: dict,
                wk_start: datetime, wk_end: datetime) -> pd.DataFrame:
    b = bookings.copy()
    b[schema["dep_time"]] = _to_naive(b[schema["dep_time"]])
    b[schema["arr_time"]] = _to_naive(b[schema["arr_time"]])
    mask = (b[schema["dep_time"]] >= wk_start) & (b[schema["dep_time"]] < wk_end)
    return b.loc[mask].copy()


# Occupation hours span wall-clock time (day-before pickup through the return
# leg, including overnight waits), so they are measured against the full
# 168-hour week, not a flyable-hours window. This keeps utilisation <= 100%.
HOURS_PER_WEEK = 168.0


def compute_utilisation(week_legs: pd.DataFrame, roster: pd.DataFrame,
                        bsch: dict, rsch: dict, metric: str,
                        wk_start: datetime, wk_end: datetime,
                        hours_available: float = HOURS_PER_WEEK) -> pd.DataFrame:
    tails = roster[rsch["tail"]].unique()

    rows = []
    for tail in tails:
        legs = week_legs[week_legs[bsch["tail"]] == tail]
        # "occupied_hrs" = total time the aircraft is committed across the week
        occupied_hrs = ((legs[bsch["arr_time"]] - legs[bsch["dep_time"]])
                        .dt.total_seconds().sum() / 3600.0)
        # cap defensively so overlapping windows can't exceed the week
        occupied_hrs = min(occupied_hrs, hours_available)
        n_legs = len(legs)
        active_days = legs[bsch["dep_time"]].dt.date.nunique()

        if metric == "hours":
            util = 100 * occupied_hrs / hours_available
        elif metric == "legs":
            util = n_legs  # raw count
        elif metric == "days":
            util = 100 * active_days / 7.0
        else:
            util = 100 * occupied_hrs / hours_available

        rows.append({
            "tail_number": tail,
            "flown_hrs": round(occupied_hrs, 2),
            "n_legs": n_legs,
            "active_days": active_days,
            "utilisation": round(util, 1),
        })

    out = pd.DataFrame(rows).sort_values("utilisation", ascending=False)
    return out.reset_index(drop=True)


def check_capacity(week_legs: pd.DataFrame, roster: pd.DataFrame,
                   bsch: dict, rsch: dict) -> pd.DataFrame:
    """Flag legs where the assigned aircraft's seats < travelling party size.

    Returns a DataFrame of violations (empty if none, or if party size isn't
    available in the bookings data).
    """
    if bsch["party"] is None:
        return pd.DataFrame()  # no party-size column to check against

    seat_lookup = dict(zip(roster[rsch["tail"]], roster[rsch["seats"]]))

    rows = []
    for _, leg in week_legs.iterrows():
        tail = leg[bsch["tail"]]
        seats = seat_lookup.get(tail)
        party = leg[bsch["party"]]
        if seats is not None and pd.notna(party) and party > seats:
            rows.append({
                "tail_number": tail,
                "seats": int(seats),
                "party_size": int(party),
                "shortfall": int(party - seats),
                "label": leg[bsch["label"]] if bsch["label"] else "",
                "route": f"{leg[bsch['dep_apt']]}→{leg[bsch['arr_apt']]}",
                "dep": leg[bsch["dep_time"]],
            })
    return pd.DataFrame(rows)


def optimise_swaps(week_legs: pd.DataFrame, roster: pd.DataFrame,
                   bsch: dict, rsch: dict,
                   turnaround_hrs: float = 1.5):
    """Smart Swap: resolve capacity shortfalls by reassigning tails within the week.

    When a leg's travelling party exceeds its aircraft's seats, search the same
    week for a larger aircraft whose own schedule leaves it free across that
    leg's time window (plus turnaround buffer). If found, swap the two tails for
    those legs. Returns:
        new_legs        : a copy of week_legs with tail assignments updated
        swap_log        : DataFrame describing each swap made
        unresolved      : DataFrame of shortfalls that could not be fixed
    The swap is conservative: it only moves the *conflicted* leg onto a larger
    tail when that tail is genuinely idle for the required window, so it never
    creates a new conflict elsewhere.
    """
    tail_col = bsch["tail"]
    dep, arr = bsch["dep_time"], bsch["arr_time"]
    seats_of = dict(zip(roster[rsch["tail"]], roster[rsch["seats"]]))

    new_legs = week_legs.copy().reset_index(drop=True)
    buf = pd.Timedelta(hours=turnaround_hrs)

    def is_free(tail, start, end, ignore_idx):
        """True if `tail` has no leg overlapping [start-buf, end+buf]."""
        sub = new_legs[(new_legs[tail_col] == tail) &
                       (new_legs.index != ignore_idx)]
        for _, r in sub.iterrows():
            if (r[dep] - buf) < end and (r[arr] + buf) > start:
                return False
        return True

    swap_log, unresolved = [], []

    if bsch["party"] is None:
        return new_legs, pd.DataFrame(), pd.DataFrame()

    # Work through shortfalls largest-first so the tightest cases get priority
    def shortfalls():
        out = []
        for idx, leg in new_legs.iterrows():
            s = seats_of.get(leg[tail_col])
            p = leg[bsch["party"]]
            if s is not None and pd.notna(p) and p > s:
                out.append((idx, int(p - s)))
        return sorted(out, key=lambda t: -t[1])

    for idx, _short in shortfalls():
        leg = new_legs.loc[idx]
        party = leg[bsch["party"]]
        cur_tail = leg[tail_col]
        start, end = leg[dep], leg[arr]

        # Candidate tails: enough seats AND free for this window
        candidates = [
            t for t, s in seats_of.items()
            if t != cur_tail and s >= party and is_free(t, start, end, idx)
        ]
        if not candidates:
            unresolved.append({
                "tail_number": cur_tail,
                "seats": int(seats_of[cur_tail]),
                "party_size": int(party),
                "shortfall": int(party - seats_of[cur_tail]),
                "route": f"{leg[bsch['dep_apt']]}→{leg[bsch['arr_apt']]}",
                "dep": start,
            })
            continue

        # Prefer the smallest sufficient aircraft (don't waste the biggest jet)
        new_tail = min(candidates, key=lambda t: seats_of[t])
        new_legs.at[idx, tail_col] = new_tail
        swap_log.append({
            "label": leg[bsch["label"]] if bsch["label"] else "",
            "route": f"{leg[bsch['dep_apt']]}→{leg[bsch['arr_apt']]}",
            "dep": start,
            "from_tail": cur_tail,
            "from_seats": int(seats_of[cur_tail]),
            "to_tail": new_tail,
            "to_seats": int(seats_of[new_tail]),
            "party_size": int(party),
        })

    return new_legs, pd.DataFrame(swap_log), pd.DataFrame(unresolved)


# ─────────────────────────────────────────────────────────────────────────────
# Charts
# ─────────────────────────────────────────────────────────────────────────────
def _empty_chart(title: str, subtitle: str, path: Path):
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor(LIGHT)
    ax.text(0.5, 0.55, "No flights scheduled this week",
            ha="center", va="center", fontsize=18, color=SLATE, weight="bold",
            transform=ax.transAxes)
    ax.text(0.5, 0.45, subtitle, ha="center", va="center",
            fontsize=11, color=SLATE, transform=ax.transAxes)
    ax.set_title(title, fontsize=15, weight="bold", color=NAVY, loc="left", pad=14)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def utilisation_chart(util_df: pd.DataFrame, metric: str,
                      wk_start: datetime, wk_label: str, path: Path):
    active = util_df[util_df["utilisation"] > 0]
    if active.empty:
        _empty_chart(f"Aircraft Utilisation — {wk_label}",
                     "All aircraft idle", path)
        return

    metric_lbl = {"hours": "Aircraft commitment (% of 168-hr week)",
                  "legs": "Number of legs flown",
                  "days": "Active days (% of 7)"}.get(metric, "Utilisation %")

    fig, ax = plt.subplots(figsize=(12, max(4, 0.45 * len(active) + 1.5)))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    colors = [STEEL if v >= active["utilisation"].median() else SKY
              for v in active["utilisation"]]
    bars = ax.barh(active["tail_number"], active["utilisation"],
                   color=colors, edgecolor="white", height=0.7)
    ax.invert_yaxis()  # highest at top

    for bar, val, hrs, legs in zip(bars, active["utilisation"],
                                   active["flown_hrs"], active["n_legs"]):
        suffix = "%" if metric != "legs" else ""
        ax.text(bar.get_width() + (active["utilisation"].max() * 0.01),
                bar.get_y() + bar.get_height()/2,
                f"{val}{suffix}  ({hrs}h, {legs} legs)",
                va="center", fontsize=9, color=NAVY)

    ax.set_xlabel(metric_lbl, fontsize=11, color=SLATE)
    ax.set_title(f"Aircraft Utilisation — {wk_label}",
                 fontsize=15, weight="bold", color=NAVY, loc="left", pad=14)
    ax.margins(x=0.18)
    ax.grid(axis="x", color="#E8EDF2", linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def utilisation_heatmap(week_legs: pd.DataFrame, roster: pd.DataFrame,
                        bsch: dict, rsch: dict,
                        wk_start: datetime, wk_end: datetime,
                        wk_label: str, path: Path):
    """Aircraft x day heatmap of hours flown — fills the canvas, no whitespace.

    Rows are aircraft that flew this week, columns are the seven days Mon-Sun,
    each cell shaded by hours flown that day. Far denser and more informative
    than a sparse bar chart.
    """
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    if week_legs.empty:
        _empty_chart(f"Aircraft Utilisation — {wk_label}", "No flights this week", path)
        return

    wl = week_legs.copy()
    wl["_hours"] = (wl[bsch["arr_time"]] - wl[bsch["dep_time"]]).dt.total_seconds() / 3600.0
    wl["_day"]   = (wl[bsch["dep_time"]].dt.normalize() - pd.Timestamp(wk_start)).dt.days
    wl = wl[(wl["_day"] >= 0) & (wl["_day"] < 7)]

    # Order aircraft by total hours (busiest at top)
    order = (wl.groupby(bsch["tail"])["_hours"].sum()
               .sort_values(ascending=False).index.tolist())

    grid = np.zeros((len(order), 7))
    for _, leg in wl.iterrows():
        r = order.index(leg[bsch["tail"]])
        grid[r, int(leg["_day"])] += leg["_hours"]

    # Cool-grey -> steel sequential colormap
    cmap = LinearSegmentedColormap.from_list(
        "tet_grey_steel", ["#F1F3F5", "#AEBccc", "#748CAB", "#3E5C76", "#1F2A37"]
    )

    fig_h = max(3.0, 0.42 * len(order) + 1.6)
    fig, ax = plt.subplots(figsize=(9, fig_h))
    fig.patch.set_facecolor("white")

    im = ax.imshow(grid, aspect="auto", cmap=cmap, vmin=0,
                   vmax=max(grid.max(), 1))

    # Annotate each non-zero cell with its hours
    for r in range(len(order)):
        for c in range(7):
            v = grid[r, c]
            if v > 0:
                txt_col = "white" if v > grid.max() * 0.55 else NAVY
                ax.text(c, r, f"{v:.1f}", ha="center", va="center",
                        fontsize=8, color=txt_col)

    day_labels = [(wk_start + timedelta(days=d)).strftime("%a\n%d %b")
                  for d in range(7)]
    ax.set_xticks(range(7)); ax.set_xticklabels(day_labels, fontsize=9)
    ax.set_yticks(range(len(order))); ax.set_yticklabels(order, fontsize=9)
    ax.set_xticks(np.arange(-0.5, 7, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(order), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)
    for s in ax.spines.values():
        s.set_visible(False)

    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("Hours flown", fontsize=9, color=SLATE)
    cbar.ax.tick_params(labelsize=8, colors=SLATE)

    ax.set_title(f"Aircraft Utilisation by Day — {wk_label}",
                 fontsize=14, weight="bold", color=NAVY, loc="left", pad=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def schedule_chart(week_legs: pd.DataFrame, roster: pd.DataFrame,
                   bsch: dict, rsch: dict,
                   wk_start: datetime, wk_end: datetime,
                   wk_label: str, path: Path,
                   capacity_issues: pd.DataFrame = None):
    if week_legs.empty:
        _empty_chart(f"Weekly Schedule — {wk_label}",
                     "No flight legs to display", path)
        return

    # Build a set of (tail, dep_time) keys that are under-capacity, for flagging
    flagged = set()
    if capacity_issues is not None and not capacity_issues.empty:
        flagged = {(r["tail_number"], r["dep"]) for _, r in capacity_issues.iterrows()}

    # Only show aircraft that actually fly this week, ordered by first departure
    order = (week_legs.groupby(bsch["tail"])[bsch["dep_time"]].min()
             .sort_values().index.tolist())
    y_pos = {tail: i for i, tail in enumerate(order)}

    fig, ax = plt.subplots(figsize=(14, max(4, 0.5 * len(order) + 1.5)))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    comps_seen = set()
    any_flagged = False
    for _, leg in week_legs.iterrows():
        tail = leg[bsch["tail"]]
        y = y_pos[tail]
        start = mdates.date2num(leg[bsch["dep_time"]])
        end = mdates.date2num(leg[bsch["arr_time"]])
        width = max(end - start, 0.02)  # min visible width
        comp = leg[bsch["comp"]] if bsch["comp"] else "Other"
        comps_seen.add(comp)
        color = COMP_COLORS.get(comp, DEFAULT_COLOR)

        is_flagged = (tail, leg[bsch["dep_time"]]) in flagged
        if is_flagged:
            any_flagged = True

        if is_flagged:
            # Strong red conflict treatment: solid red fill + thick dark edge
            ax.barh(y, width, left=start, height=0.62,
                    color=RED, edgecolor="#7B1010", linewidth=2.5, zorder=5)
            # Warning marker above the bar
            ax.plot(start + width/2, y - 0.42, marker="v", markersize=6,
                    color=RED, zorder=6)
        else:
            ax.barh(y, width, left=start, height=0.6,
                    color=color, edgecolor="white", linewidth=0.5, zorder=3)

        route = f"{leg[bsch['dep_apt']]}→{leg[bsch['arr_apt']]}"
        ax.text(start + width/2, y, route, ha="center", va="center",
                fontsize=7, color="white", weight="bold", zorder=7)

    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(order, fontsize=9)
    ax.invert_yaxis()

    # X axis: Mon–Sun day boundaries
    ax.set_xlim(mdates.date2num(wk_start), mdates.date2num(wk_end))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    for d in range(8):
        x = mdates.date2num(wk_start + timedelta(days=d))
        ax.axvline(x, color="#E8EDF2", linewidth=0.8, zorder=1)

    ax.set_title(f"Weekly Schedule — {wk_label}",
                 fontsize=15, weight="bold", color=NAVY, loc="left", pad=14)
    ax.grid(axis="x", visible=False)
    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)

    # Legend by competition
    handles = [Patch(facecolor=COMP_COLORS.get(c, DEFAULT_COLOR), label=c)
               for c in sorted(comps_seen)]
    if any_flagged:
        handles.append(Patch(facecolor=RED, edgecolor="#7B1010", linewidth=2.0,
                             label="Capacity conflict"))
    ax.legend(handles=handles, loc="upper center",
              bbox_to_anchor=(0.5, -0.08), ncol=min(4, len(handles)),
              frameon=False, fontsize=9)

    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────
def run(week=None, year=None, the_date=None,
        roster_path="fleet_roster.csv", bookings_path="flight_bookings.csv",
        metric="hours", outdir="charts"):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    roster, bookings = load_data(roster_path, bookings_path)
    rsch = detect_roster_schema(roster)
    bsch = detect_booking_schema(bookings)

    # Validate required booking fields
    required = ["tail", "dep_time", "arr_time", "dep_apt", "arr_apt"]
    missing = [k for k in required if bsch[k] is None]
    if missing:
        print("ERROR: could not detect these booking fields:", missing)
        print("Available columns:", list(bookings.columns))
        sys.exit(1)

    # Resolve the week
    if the_date is not None:
        wk_start, wk_end = week_bounds_from_date(the_date)
    elif week is not None and year is not None:
        wk_start, wk_end = week_bounds_from_isoweek(year, week)
    else:
        raise ValueError("Provide either (week & year) or a date.")

    iso = wk_start.isocalendar()
    wk_label = f"{wk_start:%d %b} – {(wk_end - timedelta(days=1)):%d %b %Y}  (Wk {iso.week})"

    week_legs = filter_week(bookings, bsch, wk_start, wk_end)
    util_df = compute_utilisation(week_legs, roster, bsch, rsch,
                                  metric, wk_start, wk_end)
    capacity_issues = check_capacity(week_legs, roster, bsch, rsch)

    # Console summary
    print(f"\nWeek: {wk_label}")
    print(f"Flight legs this week: {len(week_legs)}")
    print(f"Active aircraft: {(util_df['utilisation'] > 0).sum()} / {len(util_df)}")
    if not week_legs.empty:
        print("\nTop utilisation:")
        print(util_df.head(8).to_string(index=False))

    # Capacity check report
    if bsch["party"] is None:
        print("\n[Capacity check skipped — no party-size column found in bookings]")
    elif capacity_issues.empty:
        print("\n[Capacity check] OK — every aircraft seats its travelling party.")
    else:
        print(f"\n[Capacity check] WARNING — {len(capacity_issues)} leg(s) "
              f"under-capacity for the travelling party:")
        print(capacity_issues[["tail_number", "seats", "party_size",
                               "shortfall", "route"]].to_string(index=False))

    # Charts
    util_path = outdir / f"utilisation_{iso.year}_W{iso.week:02d}.png"
    sched_path = outdir / f"schedule_{iso.year}_W{iso.week:02d}.png"
    utilisation_chart(util_df, metric, wk_start, wk_label, util_path)
    schedule_chart(week_legs, roster, bsch, rsch,
                   wk_start, wk_end, wk_label, sched_path, capacity_issues)

    print(f"\nSaved:\n  {util_path}\n  {sched_path}")
    return util_path, sched_path, util_df, capacity_issues


def main():
    ap = argparse.ArgumentParser(description="Tetbury weekly fleet utilisation tool")
    ap.add_argument("--week", type=int, help="ISO week number (1-53)")
    ap.add_argument("--year", type=int, help="Year (used with --week)")
    ap.add_argument("--date", type=str, help="Any date in target week (YYYY-MM-DD)")
    ap.add_argument("--roster", default="fleet_roster.csv")
    ap.add_argument("--bookings", default="flight_bookings.csv")
    ap.add_argument("--metric", choices=["hours", "legs", "days"], default="hours",
                    help="Utilisation basis (default: hours flown / hours available)")
    ap.add_argument("--outdir", default="charts")
    args = ap.parse_args()

    the_date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else None
    run(week=args.week, year=args.year, the_date=the_date,
        roster_path=args.roster, bookings_path=args.bookings,
        metric=args.metric, outdir=args.outdir)


if __name__ == "__main__":
    main()
