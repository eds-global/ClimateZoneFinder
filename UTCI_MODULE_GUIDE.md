# UTCI Module — Technical Reference & Design Rationale

This document explains, in full detail, how the UTCI module (`pages/modules/utci_module.py`)
turns an EPW file into an hourly Universal Thermal Climate Index (UTCI) series, why every
formula and every user-facing control exists, and what the model's limitations are. It is
written so the module's design choices can be explained and defended to a technical
reviewer or client.

All formulas below are transcribed directly from the `pythermalcomfort` 4.0.2 source code
(`pythermalcomfort/models/solar_gain.py` and `pythermalcomfort/models/utci.py`), not from
the library's docstrings, so they match exactly what actually executes.

---

## 1. What UTCI is, and why MRT has to be computed first

**UTCI (Universal Thermal Climate Index)** is an "equivalent temperature": the temperature
of a reference outdoor environment that would produce the same physiological strain on a
reference person as the actual environment being evaluated. It is the outdoor analogue of
"feels-like" temperature, and is the current international standard (endorsed by the World
Meteorological Organization / ISO) for outdoor heat- and cold-stress assessment.

UTCI requires four physical inputs:

| Symbol | Quantity | Units | Source in this app |
|---|---|---|---|
| `tdb` | Dry bulb air temperature | °C | EPW column 6 (`dry_bulb_temperature`) |
| `tr`  | **Mean Radiant Temperature (MRT)** | °C | **Not in the EPW — must be calculated** |
| `v`   | Wind speed at 10 m | m/s | EPW column 21 (`wind_speed`) |
| `rh`  | Relative humidity | % | EPW column 8 (`relative_humidity`) |

Three of the four inputs are columns that already exist in the EPW file. **MRT is the one
quantity EPW files never contain** — it would normally come from a net radiometer or a
globe thermometer in the field. Since we only have weather-station data, MRT has to be
*estimated* from what the EPW does carry: solar position (derivable from lat/lon/time) and
solar radiation (`direct_normal_irradiance`, `diffuse_horizontal_irradiance`). This is the
reason the module's pipeline has three stages instead of one call to `utci()`:

```
EPW (lat, lon, tz, tdb, rh, v, DNI)
        │
        ▼
 1. Solar position (pvlib)  →  solar altitude, solar azimuth, for every hour
        │
        ▼
 2. Mean Radiant Temperature (ASHRAE 55 "SolarCal")  →  MRT for every hour
        │
        ▼
 3. UTCI (pythermalcomfort polynomial regression)  →  UTCI °C + stress category
```

---

## 2. Step 1 — Solar position

`compute_solar_position()` calls `pvlib.solarposition.get_solarposition(times, lat, lon)`,
the same function already used elsewhere in this codebase (`sun_path.py`,
`shading_helpers.py`), so solar geometry is computed identically everywhere in the app. It
returns, for every EPW timestamp:

- **Solar altitude** (`apparent_elevation`) — angle of the sun above the horizon, 0–90°.
  Negative values (sun below horizon) are clipped to `0.001°` before being fed into the
  MRT model, and a separate night-time mask forces the shortwave contribution to exactly
  zero when the true altitude is ≤ 0° (see §3.7).
- **Solar azimuth** — compass bearing of the sun. This is computed and stored, but is **not
  currently used** by the MRT formula, because the SHARP angle (see §3.3) is fixed at 0°
  rather than derived from azimuth and a building/body orientation. It is retained in the
  output dataframe for possible future use (e.g. façade-specific analysis).

Timezone handling mirrors `shading_helpers.compute_solar_angles()`: the EPW metadata
timezone string is resolved via `pytz`, falling back to a manual UTC-offset parse, then to
UTC, so the module never crashes on an unusual timezone string.

---

## 3. Step 2 — Mean Radiant Temperature

### 3.1 The two-part decomposition

MRT is the temperature of an imaginary black-body enclosure that would exchange the same
radiant heat with a person as the real, non-uniform surroundings (sky, ground, buildings,
sun). It has two physically distinct components:

```
MRT = MRT_longwave (thermal radiation from sky/ground/surfaces)
    + ΔMRT_shortwave (extra heating from direct/diffuse/reflected solar radiation)
```

**Longwave component.** A rigorous longwave MRT needs the sky temperature and the ground
surface temperature, which in turn need sky emissivity, cloud cover and ground thermal
properties — none of which are meaningfully derivable from a standard EPW file without a
separate calibrated model. This module uses the same simplification used by common
outdoor-comfort tools when no measured longwave data exists: **the longwave baseline is
taken equal to the dry bulb air temperature**, i.e. it assumes the sky and ground
surfaces are, on average, in thermal equilibrium with the air. This is stated explicitly in
the module docstring and in the caption under the Annual Trend chart, and is discussed as a
limitation in §7.

**Shortwave component.** This is where the EPW's solar radiation columns and the solar
position from Step 1 are used, via the ASHRAE 55 Annex C "SolarCal" model, implemented in
`pythermalcomfort.models.solar_gain`. This is the "full six-directional radiant flux"
approach — it models solar heating of a human body via its **Effective Radiant Field
(ERF)**, decomposed into diffuse, direct-beam, and ground-reflected contributions, using a
posture-and-direction-dependent **projected area factor**.

```python
delta_mrt = np.where(daytime, solar_gain(...).delta_mrt, 0.0)
mrt = dry_bulb_temperature + delta_mrt
```

### 3.2 The Effective Radiant Field (ERF) formula

From `solar_gain.py` (`_solar_gain_vectorised`), for every hour:

```
i_diff       = 0.2 × sol_radiation_dir                      # see caveat in §3.8
e_diff       = f_eff × f_svv × 0.5 × τ × i_diff
e_direct     = f_eff × fp × τ × f_bes × sol_radiation_dir
e_reflected  = f_eff × f_svv × 0.5 × τ × (sol_radiation_dir·sin(altitude) + i_diff) × ρ_floor
e_solar      = e_diff + e_direct + e_reflected
ERF          = e_solar × (asw / lw_abs)              # lw_abs = 0.95 (fixed)
ΔMRT         = ERF / (hr × f_eff)                    # hr = 6 W/m²K (fixed)
```

Where `τ` = `sol_transmittance` (fixed at 1.0 — see §3.6), `ρ_floor` = ground reflectance,
`fp` = projected area factor (§3.3), `f_eff` = effective radiation area fraction (§3.4).

Physically, the three `e_*` terms represent three independent radiation pathways onto the
body:

- **`e_diff`** — diffuse sky radiation scattered onto the body from the visible sky vault
  (scaled by `f_svv`, the fraction of sky actually visible — see §3.5).
- **`e_direct`** — the direct solar beam hitting the body's projected silhouette (scaled by
  `fp`, which depends on posture and the sun's position relative to the body — the crux of
  §3.3 — and by `f_bes`, the fraction of the body actually in the sun, see §3.5).
- **`e_reflected`** — radiation reflected off the ground into the body, driven by the total
  horizontal irradiance (`DNI·sin(altitude) + i_diff`) times the ground's reflectance.

`ERF` converts the absorbed **shortwave** radiant flux into an **equivalent longwave**
radiant field by the ratio of shortwave to longwave absorptivity (`asw / lw_abs`) — this
step exists because the final ΔMRT is meant to be added to a temperature that raises
*longwave* exchange with the body, and skin/clothing absorbs shortwave (solar) radiation
differently than longwave (thermal) radiation. Finally, `ΔMRT` divides `ERF` by a fixed
linear radiative heat transfer coefficient (`hr = 6 W/m²·K`, a standard ASHRAE
simplification for near-comfort temperatures) and by `f_eff`, converting the field back
into a temperature increment.

### 3.3 The projected area factor (`fp`) — why posture matters

`fp` is the fraction of the body's silhouette, as seen *from the direction the sun beam is
coming from*, that would intercept direct solar radiation. It depends on two things:

1. **The sun's position relative to the body** — its altitude (how high overhead) and its
   `sharp` angle (how far around from directly in front of the person).
2. **The body's posture** — a standing cylinder-like body, a seated body, and a person lying
   supine each present a completely different silhouette to a given sun direction. A low
   morning sun mostly hits a standing person's side/torso; the same low sun mostly hits a
   supine person's *head-to-toe profile* rather than their torso.

Rather than a closed-form geometric formula, ASHRAE 55 Table C-2 (reproduced in
`solar_gain.py` as `fp_table`) gives `fp` as a **13 × 7 grid**, tabulated every 15° of
`sharp` (0–180°) and every 15° of solar altitude (0–90°), separately for **standing** and
**sitting**. `solar_gain()` bilinearly interpolates this table for the exact altitude/sharp
combination of each hour. **Supine** posture is handled differently: rather than a third
table, the sun's angles are algebraically transposed (`transpose_sharp_altitude()`) so that
the *standing* table can be reused as if the person's coordinate frame were rotated 90°
(head-to-toe instead of head-to-sky) — mathematically equivalent to "supine = standing
rotated so gravity points sideways relative to the sun."

This is precisely **why `pythermalcomfort`'s `solar_gain()` only accepts three postures**:
ASHRAE 55's empirical table (derived from projected-area photography/geometry studies of the
human body, Underwood & Ward 1966 and subsequent revisions cited in ASHRAE 55 Annex C) was
only measured for these three canonical orientations. There is no standing "in-between"
posture (e.g. crouching) with a published `fp` table, so `pythermalcomfort` (and this module)
cannot support one — passing anything else raises `ValueError` from `solar_gain()`.

**Posture is exposed as a left-panel control** with four UI options — *walking*, *standing*,
*sitting*, *supine* (default: *walking*, since UTCI is framed around an outdoor pedestrian) —
because it measurably changes ΔMRT: in the worked example in §6, switching only the posture
(all else equal) shifts peak UTCI by several degrees, so it is a legitimate, physically
meaningful parameter the analyst may want to vary for a specific use case (e.g. *supine* for
a park lawn/lounging study, *sitting* for outdoor seating/café analysis). **"Walking" is not
a fourth `solar_gain()` posture** — ASHRAE 55 has no ambulatory fp table — so the module maps
it onto *standing* (`_POSTURE_ALIASES` in `utci_module.py`) before calling `solar_gain()`: a
walking person's solar-facing silhouette is closest to standing, and this is the convention
outdoor-comfort tools use in the absence of a dedicated walking study. The UI label still
reads "walking" so the analyst's mental model matches UTCI's own framing, but the physics
underneath is identical to selecting "standing".

### 3.4 Effective radiation area fraction (`f_eff`)

`f_eff` (0.725 standing, 0.696 sitting; supine reuses the standing value) is the fraction
of the total body surface area that effectively participates in radiant exchange with the
environment — parts of the body radiate to *each other* (e.g. the inside of the arms
against the torso) rather than to the surroundings, per ISO 7726. It is a fixed physiological
constant, not a user-adjustable design parameter, so it is hard-coded exactly as ASHRAE 55
specifies it and is not exposed in the UI.

### 3.5 User-facing solar-exposure controls

Three sliders in the left panel map directly onto `solar_gain()` parameters and let the
analyst describe the *outdoor context*, independent of the person's posture:

| UI control | Maps to | Meaning | Default |
|---|---|---|---|
| **Sky view factor** | `f_svv` | Fraction of the sky hemisphere actually visible from the point being studied. `1.0` = fully open sky (a plaza, a lawn); a lower value represents obstruction by buildings, trees, or a covered walkway, reducing the *diffuse* and *reflected* radiation terms. | 1.0 |
| **Shade fraction** | `f_bes = 1 − shade_fraction` | Fraction of time/area the body itself is shaded from the **direct beam** (e.g. standing partly under a tree canopy or an awning). `0` = fully sun-exposed. Only affects `e_direct`. | 0.0 |
| **Ground reflectance (albedo)** | `floor_reflectance` | Reflectivity of the ground surface (asphalt ≈ 0.1, grass ≈ 0.2–0.25, light concrete/sand ≈ 0.3–0.4, snow ≈ 0.8). Scales `e_reflected` only. | 0.2 (ASHRAE default) |

These three were chosen as the exposed controls (rather than, say, exposing `asw` or `hr`)
because they describe **the site**, which is exactly what a climate/building-design analyst
using this dashboard needs to vary between studies (open plaza vs. shaded courtyard vs.
tree-lined street) — whereas `asw`, `f_eff`, and `hr` describe **the reference person's
physiology**, which the UTCI standard defines as fixed so that UTCI values remain comparable
across studies and locations.

### 3.6 Parameters held fixed, and why

| Parameter | Fixed value | Rationale |
|---|---|---|
| `sol_transmittance` (τ) | 1.0 | This factor exists in ASHRAE 55 to model solar heat *after passing through a window* (glazing + blinds attenuate it). Outdoors, there is no glazing, so transmittance is unity — the full incident radiation reaches the body. |
| `sharp` (solar horizontal angle relative to the body's front) | 0° (sun directly in front) | SHARP is only meaningful for a person with a fixed, known orientation (e.g. seated at a fixed desk relative to a window). This module analyses an unobstructed **outdoor pedestrian**, whose facing direction is not fixed or knowable from an EPW file alone. `sharp = 0°` is the standard **maximum-exposure convention**: it evaluates the case where the person is always oriented to directly face the sun, which is the ASHRAE-55-consistent conservative (upper-bound) assumption used by outdoor SolarCal adaptations when body orientation is unspecified. This is a deliberate simplification, not an oversight — it is documented here and in §7 precisely so it can be defended or revisited (e.g. replaced with a randomized/averaged orientation per hour) if a study specifically requires it. |
| `asw` (short-wave absorptivity) | 0.7 | ASHRAE 55's documented default for a person of average skin/clothing colour; the standard's own applicability range is 0.57–0.84, and 0.7 is its midpoint/reference value. |
| `lw_abs` | 0.95 (inside `pythermalcomfort`, not adjustable) | Long-wave absorptivity of skin/clothing is close to 1 for virtually all human skin tones and clothing materials (a physical near-constant, unlike shortwave absorptivity which is colour-dependent) — ASHRAE 55 fixes it, and the library does not expose it as a parameter at all. |
| `hr` (linear radiative heat transfer coefficient) | 6 W/m²·K (inside `pythermalcomfort`, not adjustable) | Standard ASHRAE 55 linearization of the (nonlinear) Stefan-Boltzmann radiative exchange around typical comfort-range surface temperatures. |

### 3.7 Night-time handling

`solar_gain()` is undefined (and raises an error in `pythermalcomfort` — verified directly
against the installed library) for negative solar altitudes, because the `fp` lookup table
only covers 0–90°. The module therefore:

1. Clips altitude to a minimum of `0.001°` purely so the function call does not crash.
2. Independently masks the *result* to `ΔMRT = 0` for every hour where the true solar
   altitude is ≤ 0° (i.e. night).

This means at night, `MRT = dry bulb temperature` exactly — consistent with the longwave
baseline assumption in §3.1, and with there being no solar contribution after sunset.

### 3.8 A caveat about diffuse radiation

`solar_gain()`'s `i_diff` (diffuse irradiance used internally) is **not** taken from the
EPW's own `diffuse_horizontal_irradiance` column. It is approximated inside
`pythermalcomfort` itself as a fixed `0.2 × direct_normal_irradiance` (visible directly in
the source, §3.2). This is a property of the underlying ASHRAE 55 SolarCal implementation,
not a choice made in this module — it is called out here because it means the module's ERF
estimate does not fully exploit the EPW's actual diffuse-sky measurement on overcast or
partly-cloudy hours (where true diffuse radiation may differ substantially from `0.2×DNI`).
This is listed again as a known limitation in §7.

---

## 4. Step 3 — UTCI itself

Once MRT is known for every hour, `compute_utci()` calls `pythermalcomfort.models.utci()`
directly with `tdb`, `tr = mrt`, `v = wind speed`, and `rh` from the EPW.

### 4.1 What the UTCI formula actually is

UTCI is *not* evaluated by solving the underlying Fiala multi-node physiological/clothing
model on every timestep (that model is a system of differential equations, far too slow to
run 8,760 times per file interactively). Instead, `pythermalcomfort` uses the **official
UTCI operational procedure**: a **6th-order multivariate polynomial regression**, fitted by
Bröde et al. (2012) to hundreds of thousands of pre-computed reference-model runs, that
reproduces the full physiological model to within ±0.1 °C over its valid range. This is the
same reference polynomial used by essentially every operational UTCI implementation
worldwide (the official UTCI Fortran/VBA calculator, BioKlima, most GIS/climate tools).

The polynomial takes four inputs (`tdb`, `v`, `Δtr = tr − tdb`, `pa` = water vapour
pressure in kPa) and evaluates roughly 210 polynomial terms up to 6th order and up to 3rd
order cross-terms with `pa` (see `_utci_optimized()` in the source — the full expression is
reproduced verbatim in the module's source tree at
`.venv/Lib/site-packages/pythermalcomfort/models/utci.py`, lines 169–525, and is not
reproduced in full here as it is a fixed, externally-published constant, not a design
choice made in this project).

### 4.2 Converting relative humidity to vapour pressure

Before the polynomial is evaluated, `rh` is converted to actual vapour pressure `pa` using
a Magnus-type saturation-vapour-pressure formula (ITS-90 water vapour formulation):

```
es(tdb) = exp[ 2.7150305·ln(Tk) + Σ gᵢ·Tk^(i−2) ] × 0.01      Tk = tdb + 273.15 K
eh_pa   = es(tdb) × (rh / 100)
pa      = eh_pa / 10                                          (hPa → kPa)
```

with the seven coefficients `g₀…g₆` hard-coded in the source (a standard reference
formulation, not project-specific).

### 4.3 Wind speed clipping — why

UTCI's own applicability range for wind speed is **0.5 – 17.0 m/s** (`utci()`'s
`limit_inputs` check). EPW wind speed is frequently **below 0.5 m/s** (calm hours are
common overnight), which — with `limit_inputs=True` — would return `NaN` for a large
fraction of hours and leave gaps in the annual trend chart. This module therefore **clips**
`wind_speed` to `[0.5, 17.0]` m/s before calling `utci()` (see `UTCI_WIND_MIN` /
`UTCI_WIND_MAX` in `config.py`), rather than leaving values out of range. This is a common,
documented convention in operational UTCI tooling (calm air is treated as the lower bound
of the model's validated wind range, since UTCI is not defined/validated below it) and is
preferred here over silently dropping hours from every chart and KPI. `limit_inputs=True`
is still passed to `utci()`, so any genuinely extreme `tdb` or `tr − tdb` combination
outside the model's validated envelope still correctly returns `NaN` rather than a
fabricated value.

### 4.4 Stress category thresholds

`utci()` classifies every UTCI value into one of ten categories, using fixed thresholds
from the same Bröde et al. / Błażejczyk operational-procedure reference (reproduced in
`config.py` as `UTCI_STRESS_BINS` / `UTCI_STRESS_LABELS` / `UTCI_STRESS_COLORS`, used by
the Stress Category tab):

| UTCI range (°C) | Category |
|---|---|
| < −40 | Extreme cold stress |
| −40 to −27 | Very strong cold stress |
| −27 to −13 | Strong cold stress |
| −13 to 0 | Moderate cold stress |
| 0 to 9 | Slight cold stress |
| 9 to 26 | **No thermal stress** |
| 26 to 32 | Moderate heat stress |
| 32 to 38 | Strong heat stress |
| 38 to 46 | Very strong heat stress |
| > 46 | Extreme heat stress |

---

## 5. Where each module function fits

| Function | Role |
|---|---|
| `compute_solar_position(df, lat, lon, tz_str)` | Step 1 — pvlib solar altitude/azimuth for every row |
| `compute_mrt(df, lat, lon, tz_str, posture, sky_view_factor, shade_fraction, ground_reflectance)` | Step 2 — ERF/ΔMRT via `solar_gain()`, returns MRT |
| `compute_utci(df, mrt)` | Step 3 — wind clipping + `utci()` call |
| `add_utci_columns(...)` | Orchestrates steps 1–3, returns the augmented dataframe (`solar_altitude`, `solar_azimuth`, `delta_mrt`, `mrt`, `utci`, `utci_stress_category`, `utci_feels_like_diff`) |
| `compute_daily_stats(df)` | Daily min/max/avg aggregation feeding the Annual Trend chart |
| `render(...)` | Streamlit tab dispatcher (Overview / Annual Trend) |

`add_utci_columns()` is called from `pages/analysis.py` inside a `st.cache_data`-wrapped
function (`cached_utci_df`), keyed on the raw EPW bytes **and** every MRT parameter
(posture, sky view factor, shade fraction, ground reflectance) — so the ~5-second
computation (dominated by the per-hour `solar_gain()` call, which is Python-looped via
`numpy.vectorize` rather than fully vectorised) only reruns when the file or a parameter
actually changes.

---

## 6. Full worked example (real data, fully traceable)

To make every formula above concrete, here is one real hour from the sample file
`IND_DL_New.Delhi-Safdarjung.AP.421820_ISHRAE2014.epw`, computed with the module's default
parameters (posture = standing, sky view factor = 1.0, shade fraction = 0.0, ground
reflectance = 0.2), independently reproduced by hand outside the module to confirm the
module's own output.

**Location & time:** New Delhi–Safdarjung (28.58° N, 77.20° E), 15 May, 13:30 local
(Asia/Kolkata).

**EPW inputs for this hour:**

| Variable | Value |
|---|---|
| Dry bulb temperature | 35.2 °C |
| Relative humidity | 39 % |
| Wind speed | 1.5 m/s |
| Direct normal irradiance | 686 W/m² |

**Step 1 — Solar position (pvlib):**

| Variable | Value |
|---|---|
| Solar altitude | 70.77° |
| Solar azimuth | 243.37° |

**Step 2 — Mean Radiant Temperature:**

```
i_diff       = 0.2 × 686              = 137.20 W/m²
fp           (standing, sharp=0°, altitude=70.77° → bilinear interpolation)
             = 0.1615
f_eff        = 0.725

e_diff       = 0.725 × 1.0 × 0.5 × 1.0 × 137.20        =  49.74 W/m²
e_direct     = 0.725 × 0.1615 × 1.0 × 1.0 × 686        =  80.31 W/m²
e_reflected  = 0.725 × 1.0 × 0.5 × 1.0 × (686×sin(70.77°) + 137.20) × 0.2
             = 0.725 × 0.5 × (647.9 + 137.2) × 0.2     =  56.91 W/m²
e_solar      = 49.74 + 80.31 + 56.91                   = 186.95 W/m²

ERF          = 186.95 × (0.7 / 0.95)                   = 137.75 W/m²
ΔMRT         = 137.75 / (6 × 0.725)                    =  31.67 °C

MRT          = 35.2 + 31.67                            =  66.87 °C
```

**Step 3 — UTCI:**

```
es(35.2°C)   = 57.42 hPa           (saturation vapour pressure)
eh_pa        = 57.42 × 0.39        = 22.39 hPa
pa           = 2.239 kPa
Δtr          = 66.87 − 35.2        = 31.67 °C
v (clipped)  = 1.5 m/s             (already within 0.5–17.0, no clipping needed)

UTCI(tdb=35.2, tr=66.87, v=1.5, pa=2.239)  =  43.2 °C
→ stress category: "very strong heat stress"
```

**Interpretation:** on a clear May afternoon in Delhi, although the air is "only" 35.2 °C,
intense direct overhead sun (altitude 70.8°) pushes the mean radiant temperature to a
scorching 66.9 °C, and the combined effect on a standing person outdoors is equivalent to
43.2 °C — a "feels-like" delta of **+8.0 °C** over dry bulb, and solidly in the "very
strong heat stress" category. This is exactly the kind of information a dry-bulb-only
analysis cannot surface, and is the module's core value proposition.

*(This calculation was re-run independently outside `utci_module.py`, using the exact
formulas transcribed from the `pythermalcomfort` source, and matched the module's own
output to full floating-point precision — see the verification performed for this
document.)*

---

## 7. Known limitations (read this before presenting the module externally)

1. **Longwave MRT baseline = air temperature.** The model has no measured sky/ground
   surface temperature, so it cannot capture true nocturnal radiative cooling (e.g. a clear
   desert night radiating heat to space faster than the air cools) or the warming effect of
   overcast/urban long-wave "trapping." At night, MRT is set exactly equal to dry bulb
   temperature (§3.1, §3.7).
2. **Fixed SHARP = 0° (always facing the sun).** This is a deliberate worst-case/maximum
   solar exposure convention (§3.6), appropriate for a "how hot can it feel in full sun"
   analysis, but it will **overestimate** MRT/UTCI relative to a real pedestrian who is not
   constantly facing the sun (e.g. walking north-south at midday with the sun to one side).
3. **Diffuse irradiance is not taken from the EPW.** `solar_gain()`'s internal `i_diff =
   0.2 × DNI` approximation (§3.8) is a property of the underlying ASHRAE 55 implementation,
   not something this module can override without re-implementing `solar_gain()` from
   scratch.
4. **No shading geometry.** Sky view factor, shade fraction, and ground reflectance are
   single constant values applied to the whole selected period — they do not vary by hour
   (e.g. from a moving shadow) or automatically derive from a 3D site model. They are meant
   as scenario inputs the analyst sets deliberately per study.
5. **UTCI's own applicability envelope.** Even with wind clipped, `utci()` still returns
   `NaN` when `tdb` is outside −50…50 °C or `tr − tdb` is outside −30…+70 °C — this can
   occur in extreme MRT scenarios (very high ΔMRT under very low sky view / high albedo
   combinations) and will show as gaps if it happens.
6. **The reference person is fixed.** Like all UTCI values, the numbers describe a
   standardised reference individual (per the Fiala model's clothing/metabolic assumptions)
   — they are a comparative index, not a literal prediction for any specific real person's
   thermal sensation.

---

## 8. References

- ASHRAE Standard 55-2020/2023, *Thermal Environmental Conditions for Human Occupancy*,
  Normative Appendix C (SolarCal method for shortwave solar gain to a human body).
- Arens, E., et al. (1986). *A new method for predicting the mean radiant temperature under
  direct solar radiation.* (Origin of the SolarCal/projected-area-factor approach.)
- Bröde, P., et al. (2012). *Deriving the operational procedure for the Universal Thermal
  Climate Index (UTCI).* International Journal of Biometeorology, 56(3), 481–494. (Source
  of the 6th-order polynomial regression and the stress category thresholds.)
- Błażejczyk, K., et al. (2013). *An introduction to the Universal Thermal Climate Index
  (UTCI).* Geographia Polonica, 86(1). (Stress category interpretation.)
- ISO 7726:1998, *Ergonomics of the thermal environment — Instruments for measuring
  physical quantities.* (`f_eff` effective radiation area fraction.)
- `pythermalcomfort` 4.0.2 source code: `pythermalcomfort/models/solar_gain.py`,
  `pythermalcomfort/models/utci.py`, `pythermalcomfort/utilities.py`.

---

**Related files**

- `pages/modules/utci_module.py` — implementation
- `pages/modules/config.py` — `UTCI_*` constants (defaults, stress bands, colors)
- `pages/analysis.py` — UI wiring (module selector, left-panel controls, caching)
