# ==============================================================================
# TETBURY AVIATION — EUROPEAN FOOTBALL FLEET SIZING & CADENCE MODEL
# Task 3: Aircraft requirements + weekly utilisation cadence
# ==============================================================================
# Runs top-to-bottom. Sections:
#   0. Setup
#   1. Load & inspect
#   2. Data quality fix (UCL/UECL qualifying date redistribution)
#   3. European scope filter + competition-level kickoff occupation windows
#   4. Fleet sizing (concurrent occupancy)
#   5. Demand distribution & two-tier recommendation
#   6. Weekly cadence model (job-chaining with repositioning)
# ==============================================================================

# ---- 0. SETUP ----------------------------------------------------------------
library(tidyverse)
library(readxl)
library(lubridate)
library(geosphere)

# EDIT THIS PATH to wherever the file sits on your machine
DATA_PATH <- "/Users/robertvanhamelplaterink/PSTAT126/Sports Aviation Demand Calendar for TMH - Final Version 2.xlsx"

# ---- 1. LOAD & INSPECT -------------------------------------------------------
df <- read_excel(DATA_PATH, sheet = "All Fixtures")

glimpse(df)

# Missing values by column
df %>%
  summarise(across(everything(), ~sum(is.na(.)))) %>%
  pivot_longer(everything(), names_to = "column", values_to = "n_missing") %>%
  filter(n_missing > 0) %>%
  arrange(desc(n_missing))

range(df$Date, na.rm = TRUE)

# ---- 2. DATA QUALITY FIX -----------------------------------------------------
# Projected UCL/UECL qualifying fixtures (2025-2030) were all stamped on a single
# placeholder date (08 July). Redistribute them across the real 2024 round schedule.

# Build 2024 templates: each qualifying date as a day-offset from 01 July
build_template <- function(comp) {
  df %>%
    filter(Competition == comp) %>%
    mutate(match_date = as.Date(Date)) %>%
    filter(year(match_date) == 2024) %>%
    count(match_date) %>%
    mutate(offset_days = as.integer(match_date - as.Date("2024-07-01")))
}

ucl_template  <- build_template("UCL Qualifying")
uecl_template <- build_template("UECL Qualifying")

# Redistribute rows of one competition/year proportionally across the template
redistribute_dates <- function(data, template, base_year) {
  set.seed(42)  # reproducible
  sampled_offsets <- sample(
    template$offset_days,
    size    = nrow(data),
    replace = TRUE,
    prob    = template$n / sum(template$n)
  )
  data %>% mutate(Date = as.Date(paste0(base_year, "-07-01")) + sampled_offsets)
}

df_fixed <- df %>% mutate(Date = as.Date(Date))

for (yr in 2025:2030) {
  for (comp in c("UCL Qualifying", "UECL Qualifying")) {
    tmpl <- if (comp == "UCL Qualifying") ucl_template else uecl_template
    idx  <- which(df_fixed$Competition == comp & year(df_fixed$Date) == yr)
    if (length(idx) > 0) {
      df_fixed$Date[idx] <- redistribute_dates(df_fixed[idx, ], tmpl, yr)$Date
    }
  }
}

# Verify: projected years should now span multiple dates, not one
df_fixed %>%
  filter(Competition %in% c("UCL Qualifying", "UECL Qualifying"),
         year(Date) %in% 2024:2030) %>%
  mutate(year = year(Date)) %>%
  group_by(Competition, year) %>%
  summarise(n_fixtures = n(), n_dates = n_distinct(Date), .groups = "drop") %>%
  arrange(Competition, year) %>%
  print(n = Inf)

# ---- 3. EUROPEAN SCOPE + KICKOFF OCCUPATION WINDOWS --------------------------
european_competitions <- c(
  "Premier League", "Bundesliga", "Serie A", "La Liga", "Ligue 1",
  "UEFA Champions League", "UEFA Europa League", "UEFA Conference League",
  "UEFA Women's Champions League", "UCL Qualifying", "UEL Qualifying",
  "UECL Qualifying", "UWCL Qualifying", "UEFA EURO 2024", "UEFA EURO 2028",
  "UEFA EURO Qualifiers", "UEFA Nations League", "UEFA Nations League 2028-29",
  "UEFA Nations League 2030-31", "UEFA U-21 European Championship 2027",
  "UEFA U-21 European Championship 2029", "UEFA Women's European Championship 2029",
  "WC Qualifiers (UEFA)", "WC Qualifiers (UEFA) 2028-30"
)

# Competition-level kickoff assumptions (replace flat 20:00)
kickoff_times <- tribble(
  ~Competition,                                ~kickoff_hour, ~kickoff_min,
  "UEFA Champions League",                      21, 0,
  "UEFA Europa League",                         21, 0,
  "UEFA Conference League",                     21, 0,
  "UCL Qualifying",                             20, 0,
  "UEL Qualifying",                             20, 0,
  "UECL Qualifying",                            20, 0,
  "UWCL Qualifying",                            19, 0,
  "UEFA Women's Champions League",              19, 0,
  "Premier League",                             15, 0,
  "Bundesliga",                                 15, 30,
  "Serie A",                                    18, 0,
  "La Liga",                                    18, 30,
  "Ligue 1",                                    17, 0,
  "UEFA EURO 2024",                             21, 0,
  "UEFA EURO 2028",                             21, 0,
  "UEFA EURO Qualifiers",                       20, 45,
  "UEFA Nations League",                        20, 45,
  "UEFA Nations League 2028-29",                20, 45,
  "UEFA Nations League 2030-31",                20, 45,
  "UEFA U-21 European Championship 2027",       18, 0,
  "UEFA U-21 European Championship 2029",       18, 0,
  "UEFA Women's European Championship 2029",    18, 0,
  "WC Qualifiers (UEFA)",                       20, 45,
  "WC Qualifiers (UEFA) 2028-30",               20, 45
)

# Occupation window per fixture:
#   start  = 12:00 the day before the match (plane collects team)
#   end    = kickoff + 2h match + 3h departure window + return block time
df_euro <- df_fixed %>%
  filter(Competition %in% european_competitions, `Distance (km)` >= 300) %>%
  left_join(kickoff_times, by = "Competition") %>%
  mutate(
    match_date       = as.Date(Date),
    kickoff_time     = as.POSIXct(match_date) + hours(kickoff_hour) + minutes(kickoff_min),
    match_end        = kickoff_time + hours(2),
    plane_departs    = match_end + hours(3),
    occupation_start = as.POSIXct(match_date - 1) + hours(12),
    occupation_end   = plane_departs + (`Est Block Return Flight Time` * 3600)
  )

# Sanity check — any competition that failed the kickoff join shows up here
stopifnot(nrow(filter(df_euro, is.na(kickoff_hour))) == 0)

# ---- 4. FLEET SIZING (CONCURRENT OCCUPANCY) ----------------------------------
events <- df_euro %>%
  select(occupation_start, occupation_end) %>%
  pivot_longer(everything(), names_to = "event_type", values_to = "time") %>%
  mutate(delta = if_else(event_type == "occupation_start", 1, -1)) %>%
  arrange(time) %>%
  mutate(concurrent_planes = cumsum(delta))

cat("Absolute peak concurrent aircraft:", max(events$concurrent_planes), "\n")

# Annual peaks
annual_peaks <- events %>%
  mutate(year = year(time)) %>%
  group_by(year) %>%
  summarise(peak_fleet = max(concurrent_planes), .groups = "drop") %>%
  filter(year %in% 2026:2031)
print(annual_peaks)

# ---- 5. DEMAND DISTRIBUTION & TWO-TIER RECOMMENDATION ------------------------
daily_peaks <- events %>%
  mutate(date = as.Date(time)) %>%
  group_by(date) %>%
  summarise(daily_peak = max(concurrent_planes), .groups = "drop") %>%
  arrange(desc(daily_peak))

cat("\nDaily peak distribution:\n")
print(summary(daily_peaks$daily_peak))

cat("\nFleet size covering X% of days:\n")
print(quantile(daily_peaks$daily_peak, probs = c(0.75, 0.90, 0.95, 0.99, 1.0)))

# Recommended core fleet = 95th percentile (data-derived), with 40 as buffered rec
core_95 <- as.integer(quantile(daily_peaks$daily_peak, 0.95))
cat("\n95th percentile (data-derived core fleet):", core_95, "\n")

# Two-tier visualisation
daily_peaks %>%
  mutate(year = year(date)) %>%
  filter(year %in% 2026:2031) %>%
  ggplot(aes(x = date, y = daily_peak)) +
  geom_col(aes(fill = daily_peak > core_95), width = 1) +
  scale_fill_manual(
    values = c("FALSE" = "steelblue", "TRUE" = "firebrick"),
    labels = c(paste0("Core fleet (<=", core_95, ")"),
               paste0("Sub-charter (>", core_95, ")"))
  ) +
  geom_hline(yintercept = core_95, linetype = "dashed") +
  labs(title = "Daily Aircraft Demand — European Football 2026-2031 (Corrected)",
       subtitle = paste0("Dashed line = ", core_95, " aircraft (95th percentile)"),
       x = NULL, y = "Aircraft Required", fill = NULL) +
  theme_minimal()

# ==============================================================================
# 6. WEEKLY CADENCE MODEL — JOB-CHAINING WITH REPOSITIONING
# ==============================================================================
# Goal: for a representative week, assign fixtures ("jobs") to the fewest planes
# by chaining jobs together. A plane finishing one job can fly (empty) to collect
# the next team if it can arrive in time, plus a ground turnaround buffer.
# Outputs the four requested metrics:
#   (a) planes needed for the week
#   (b) total fleet idle hours
#   (c) max consecutive idle per plane
#   (d) repositioning (empty-leg) km flown

# ---- 6a. Build the job table for a representative week -----------------------
# Week chosen to contain BOTH a UEFA midweek round and a domestic weekend round.
rep_week_start <- as.Date("2027-10-18")  # Monday
rep_week_end   <- rep_week_start + 7

jobs <- df_euro %>%
  filter(match_date >= rep_week_start, match_date < rep_week_end) %>%
  transmute(
    competition      = Competition,
    team             = `Away Team`,
    pickup_iata      = `Away IATA (From)`,
    pickup_lat       = `Away Lat`,
    pickup_lon       = `Away Long`,
    drop_iata        = `Home IATA`,
    drop_lat         = `Home Lat`,
    drop_lon         = `Home Long`,
    occupation_start,
    occupation_end,
    block_return_hrs = `Est Block Return Flight Time`,
    distance_km      = `Distance (km)`
  ) %>%
  arrange(occupation_start) %>%
  mutate(job_id = row_number())

cat("\n\n=== WEEKLY CADENCE MODEL ===\n")
cat("Representative week:", format(rep_week_start), "to", format(rep_week_end), "\n")
cat("Jobs in week:", nrow(jobs), "\n\n")

# Day x competition shape of the week
jobs %>%
  mutate(day = wday(occupation_start, label = TRUE, week_start = 1)) %>%
  count(day, competition) %>%
  pivot_wider(names_from = competition, values_from = n, values_fill = 0) %>%
  print()

# ---- 6b. Assignment engine --------------------------------------------------
TURNAROUND_HRS <- 1.5    # ground time between jobs (clean, refuel, crew)
REPO_SPEED_KMH <- 800    # business-jet cruise for empty repositioning legs

repo_km <- function(lat1, lon1, lat2, lon2) {
  distHaversine(c(lon1, lat1), c(lon2, lat2)) / 1000
}

assign_fleet <- function(jobs) {
  jobs <- jobs %>% arrange(occupation_start)
  planes <- list()  # each: drop_lat, drop_lon, free_time

  jobs$plane_id <- NA_integer_
  jobs$repo_km  <- 0
  jobs$idle_hrs <- 0

  for (i in seq_len(nrow(jobs))) {
    job <- jobs[i, ]
    best_plane <- NA_integer_
    best_idle  <- Inf

    for (p in seq_along(planes)) {
      pl    <- planes[[p]]
      rkm   <- repo_km(pl$drop_lat, pl$drop_lon, job$pickup_lat, job$pickup_lon)
      rhrs  <- rkm / REPO_SPEED_KMH
      ready <- pl$free_time + (rhrs * 3600) + (TURNAROUND_HRS * 3600)
      if (ready <= job$occupation_start) {
        idle <- as.numeric(job$occupation_start - pl$free_time) / 3600 - rhrs - TURNAROUND_HRS
        if (idle < best_idle) { best_idle <- idle; best_plane <- p }
      }
    }

    if (is.na(best_plane)) {
      planes[[length(planes) + 1]] <- list(
        drop_lat = job$drop_lat, drop_lon = job$drop_lon,
        free_time = job$occupation_end
      )
      jobs$plane_id[i] <- length(planes)
    } else {
      pl <- planes[[best_plane]]
      jobs$repo_km[i]  <- repo_km(pl$drop_lat, pl$drop_lon,
                                  job$pickup_lat, job$pickup_lon)
      jobs$idle_hrs[i] <- best_idle
      planes[[best_plane]]$drop_lat  <- job$drop_lat
      planes[[best_plane]]$drop_lon  <- job$drop_lon
      planes[[best_plane]]$free_time <- job$occupation_end
      jobs$plane_id[i] <- best_plane
    }
  }
  list(jobs = jobs, n_planes = length(planes))
}

result    <- assign_fleet(jobs)
jobs_done <- result$jobs

# ---- 6c. The four metrics ---------------------------------------------------
total_idle_hrs <- sum(jobs_done$idle_hrs)
repo_total_km  <- sum(jobs_done$repo_km)

per_plane <- jobs_done %>%
  group_by(plane_id) %>%
  summarise(
    n_jobs        = n(),
    busy_hrs      = sum(as.numeric(occupation_end - occupation_start) / 3600),
    idle_hrs      = sum(idle_hrs),
    max_idle_hrs  = max(idle_hrs),
    repo_km       = sum(repo_km),
    .groups = "drop"
  ) %>%
  arrange(plane_id)

week_hrs <- as.numeric(difftime(rep_week_end, rep_week_start, units = "hours"))

cat("\n--- WEEKLY CADENCE RESULTS ---\n")
cat("(a) Planes needed for the week :", result$n_planes, "\n")
cat("(b) Total fleet idle hours     :", round(total_idle_hrs, 1), "\n")
cat("(c) Max consecutive idle (1 plane):", round(max(jobs_done$idle_hrs), 1), "hrs\n")
cat("(d) Total repositioning km     :", format(round(repo_total_km), big.mark = ","), "\n")

cat("\nFleet utilisation rate        :",
    round(100 * sum(per_plane$busy_hrs) /
          (result$n_planes * week_hrs), 1), "%\n")
cat("Avg jobs per plane            :", round(mean(per_plane$n_jobs), 1), "\n")
cat("Avg repositioning km per plane:",
    format(round(mean(per_plane$repo_km)), big.mark = ","), "\n")

cat("\nPer-plane summary:\n")
print(per_plane, n = Inf)

# ---- 6d. Gantt chart of the week --------------------------------------------
jobs_done %>%
  mutate(plane_lbl = factor(sprintf("Plane %02d", plane_id),
                            levels = sprintf("Plane %02d", sort(unique(plane_id))))) %>%
  ggplot(aes(y = fct_rev(plane_lbl))) +
  geom_segment(aes(x = occupation_start, xend = occupation_end,
                   yend = fct_rev(plane_lbl), colour = competition),
               linewidth = 4) +
  labs(title = "Weekly Aircraft Cadence — Representative Week (Oct 2027)",
       subtitle = paste0(result$n_planes, " aircraft  ·  ",
                         nrow(jobs), " fixtures  ·  job-chaining with repositioning"),
       x = NULL, y = NULL, colour = "Competition") +
  theme_minimal(base_size = 11) +
  theme(legend.position = "bottom")

# ---- 6e. Sensitivity: tighten departure window 3h -> 2h ---------------------
# Quick rerun to show how the post-match departure assumption moves the numbers.
jobs_2h <- df_euro %>%
  filter(match_date >= rep_week_start, match_date < rep_week_end) %>%
  mutate(occupation_end = (kickoff_time + hours(2) + hours(2)) +
                          (`Est Block Return Flight Time` * 3600)) %>%
  transmute(
    competition = Competition, team = `Away Team`,
    pickup_lat = `Away Lat`, pickup_lon = `Away Long`,
    drop_lat = `Home Lat`, drop_lon = `Home Long`,
    occupation_start, occupation_end,
    block_return_hrs = `Est Block Return Flight Time`,
    distance_km = `Distance (km)`
  ) %>%
  arrange(occupation_start) %>%
  mutate(job_id = row_number())

result_2h <- assign_fleet(jobs_2h)
cat("\n--- SENSITIVITY: 2-hour departure window ---\n")
cat("Planes needed (2h window):", result_2h$n_planes,
    "  vs 3h window:", result$n_planes, "\n")
