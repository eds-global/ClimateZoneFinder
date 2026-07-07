"""Shared configuration constants for Climate Zone Finder analysis modules."""

# ── ASHRAE 55 Adaptive Comfort ─────────────────────────────────────────────────
ASHRAE_ALPHA = 0.9              # Exponential running mean coefficient
ASHRAE_T_PMA_MIN = 10.0         # Minimum applicable prevailing mean temp (°C)
ASHRAE_T_PMA_MAX = 33.5         # Maximum applicable prevailing mean temp (°C)
ASHRAE_COMFORT_NEUTRAL_A = 0.31 # Coefficient: T_comf = A * T_pma + B
ASHRAE_COMFORT_NEUTRAL_B = 17.8

# ── Comfort Bands ──────────────────────────────────────────────────────────────
COMFORT_BAND_80_PCT = 3.5       # ± °C around neutral for 80% acceptability
COMFORT_BAND_90_PCT = 2.5       # ± °C around neutral for 90% acceptability

# ── Degree Hours ───────────────────────────────────────────────────────────────
CDH_BASE_TEMP = 24.0            # Cooling degree-hours base temperature (°C)
HDH_BASE_TEMP = 18.0            # Heating degree-hours base temperature (°C)

# ── Thermal Comfort Strategies ─────────────────────────────────────────────────
NV_MIN_WIND_SPEED = 1.0         # Min wind speed for natural ventilation (m/s)
NV_COOL_DBT_THRESHOLD = 24.0    # DBT above which NV is counted (°C)
NIGHT_FLUSH_DIURNAL_MIN = 8.0   # Min diurnal range for night flushing (°C)
MECH_COOLING_RH_THRESHOLD = 60.0 # RH above which mech. cooling preferred over evap (%)

# ── Wind Analysis ──────────────────────────────────────────────────────────────
CALM_WIND_THRESHOLD = 0.5       # Calm wind cutoff (m/s, WMO convention)
WIND_SPEED_BINS = [0, 2, 4, 6, 8, 10, 15, 100]
WIND_SPEED_LABELS = ["0-2", "2-4", "4-6", "6-8", "8-10", "10-15", "15+"]

# ── Shading / Radiation ────────────────────────────────────────────────────────
DEFAULT_TEMP_THRESHOLD = 28.0   # Default overheating temperature (°C)
DEFAULT_RAD_THRESHOLD = 315.0   # Default radiation threshold (W/m²)
DEFAULT_CUTOFF_ANGLE = 45.0     # Default design cutoff angle (degrees)

# ── Humidity Comfort ───────────────────────────────────────────────────────────
RH_COMFORT_MIN = 30.0           # Lower bound of comfort RH band (%)
RH_COMFORT_MAX = 65.0           # Upper bound of comfort RH band (%)

# ── Psychrometrics ─────────────────────────────────────────────────────────────
P_ATM = 101_325.0               # Standard atmospheric pressure (Pa)

# ── Rainfall ───────────────────────────────────────────────────────────────────
DEFAULT_HEAVY_RAIN_THRESHOLD = 50.0   # mm/day
RUNOFF_COEFF_ROOF   = 0.90
RUNOFF_COEFF_PAVED  = 0.90
RUNOFF_COEFF_GREEN  = 0.10
RUNOFF_COEFF_WATER  = 0.90
VALID_GI_PERCENTILES = [85, 90, 95, 98]

# ── UTCI / Mean Radiant Temperature ─────────────────────────────────────────────
UTCI_DEFAULT_POSTURE           = "walking"  # UI label; modeled as "standing" (no ASHRAE 55 walking fp table)
UTCI_DEFAULT_SKY_VIEW_FACTOR   = 1.0         # 1 = fully open sky, lower = obstructed
UTCI_DEFAULT_SHADE_FRACTION    = 0.0         # Fraction of time body is shaded from direct sun
UTCI_DEFAULT_GROUND_REFLECTANCE = 0.2        # Ground/floor albedo used by SolarCal
UTCI_ASW = 0.7                               # Average short-wave absorptivity (skin/clothing)
UTCI_WIND_MIN = 0.5                          # UTCI model's lower valid wind-speed bound (m/s)
UTCI_WIND_MAX = 17.0                         # UTCI model's upper valid wind-speed bound (m/s)

# UTCI thermal stress categories (°C) — 10 bands from extreme cold to extreme heat
UTCI_STRESS_BINS = [-100, -40, -27, -13, 0, 9, 26, 32, 38, 46, 100]
UTCI_STRESS_LABELS = [
    "extreme cold stress", "very strong cold stress", "strong cold stress",
    "moderate cold stress", "slight cold stress", "no thermal stress",
    "moderate heat stress", "strong heat stress", "very strong heat stress",
    "extreme heat stress",
]
# Official UTCI thermal-stress color scale, cold → hot (matches the standard
# UTCI legend used in operational tools, e.g. the UTCI-Fiala/BioKlima charts).
UTCI_STRESS_COLORS = [
    "#4D235F", "#224898", "#3288BD", "#67BCD4", "#8CD9D8",
    "#74B761", "#EEB84A", "#E97A2E", "#CE2029", "#7A1A22",
]
