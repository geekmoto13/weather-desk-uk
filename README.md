This build includes an Easter egg. Can you find it?

# Weather Desk UK & Ireland — version 1.3.0 (build UK20-test)

A separate local UK edition, using port **8766** alongside the US edition's 8765.
The weather postcard, radar, beacon, calendar, historical comparisons, coastal
tides and Radio & Space retain their existing layout and features.

screenshot here: ![Screenshot](docs/screenshot.png)

## What's new in 1.3.0

- **Network access is back.** Start the dashboard with `START-WEATHER-DESK-UK-LAN.bat` (or add `--lan`)
  to open it from a phone or tablet on your home network. See **Network access
  (optional)** at the end of this file. The normal start files remain local-only.
- **Radio & Space has been rewritten.** It uses the full page width, with compact
  HF and reception panels, a large propagation map, NOAA solar imagery and
  measurement plots. HF forecasts are repaired, destinations can be picked by
  name or prefix without a grid, there are two prediction models (KC2G and
  VOACAP), and recent PSK Reporter reception reports are shown. Details are in
  the **Radio & Space** section.
- **Experimental temperature calendar.** Calendar colours now use the historical
  middle third for each date instead of a fixed ±1°F band. Details are in
  **Readings and comparisons**.
- **Overview layout and 48-hour outlook.** The Berkeley beacon gets a larger,
  horizontal building and text area. The smaller radar sits above a new 48-hour
  forecast made of four rolling 12-hour windows. Each shows the prevailing
  description, the range of hourly temperatures in °F/°C, the highest hourly
  precipitation chance and the highest hourly wind speed. This is not a 12-hour
  rain probability or a gust forecast. Missing numbers stay unavailable and
  incomplete windows withhold ranges. The outlook is separate from station
  readings and from the 12-hour beacon calculation. It uses NOAA/NWS hourly forecasts in the US, Open-Meteo / DWD ICON for UK locations and Met Éireann for Ireland. Met Éireann's current point feed has no probability field, so its precipitation chance stays unavailable.
  The same cached feed serves the beacon and the outlook; no extra key is
  needed. Large warnings or long source descriptions can make the page scroll
  rather than clip content.

## Dingle tide and source-label update

Irish coastal levels now locate a reporting gauge using a small recent query,
then load only that port's 24-hour history. Dingle Harbour is in the network.
The page remains responsive while readings load. A failed refresh keeps the
last known chart, marked delayed; a partial history is explicitly identified.
Curves are remembered locally for the next startup. Source failures include a
reason and retry automatically. Unknown-quality levels are labelled provisional.

The Official public station option selects Met Éireann in the Republic of
Ireland; it is no longer described as an airport. Reports older than 90 minutes
remain delayed and retain their original observation time. A slow reporting
feed is not made current by refreshing the page.

When CWOP is chosen in Ireland, temperature/wind and the rainfall ledger may
come from different, explicitly named stations. The CWOP/MADIS connection
provides short-period rain reports, not month/year counters. The Irish ledger
uses its separately named Met Éireann gauge. Missing hours leave month/YTD
unavailable: the display now says “Incomplete history”, and Gauge details gives
the missing-interval count. Rainfall figures are never combined across stations
or filled with zeros to manufacture a full calendar total. Irish rain details
now carry Met Éireann attribution rather than an Environment Agency caption.

## Republic of Ireland locations

In Settings, find your town (for example Dublin, Cork or Galway), choose the
result and save. Maidenhead grids are also supported; their centre determines
the country. A UK postcode still works for UK locations. Eircode lookup is not
included. The saved provider summary identifies the selected country.

In the Republic, Automatic public weather uses the nearest usable Met Éireann
EDR station within 150 km reporting both temperature and measured hourly rain.
A reported zero qualifies; missing rain does not. A connected Ambient personal
station keeps priority, and explicitly choosing CWOP remains an override.
Forecast and warnings still use the selected country's sources. Northern Ireland
keeps the UK providers. A packaged country boundary is used, rather than treating
the whole island or a rectangular area as the Republic.

Irish forecasts use Met Éireann's point forecast; their symbols describe the
preceding hour, as documented by the agency. The existing prevailing twelve-hour
beacon rule is retained. The public website JSON is checked every 15 minutes;
its retrieval time is shown, not an invented model issue time. Feed/schema
failures leave the beacon unlit. All active/future Republic of Ireland land
warnings are displayed with their original headlines and affected areas, with
an explicit nationwide scope. They are checked each minute; a failed check is
unknown, never a reassuring “no warnings”. The app does not claim to be endorsed
by Met Éireann.

Irish daily records and calculated 1991–2020 references use the nearest of seven
Met Éireann climate stations. These are distinct, explicitly named reference
sites; they may differ from the current observation station. The bundled public
archive runs through 30 September 2026 and refreshes from the source monthly
files. Each normal needs 24 of 30 valid years (7 of 8 leap days). Estimates and
ambiguous accumulated rain are excluded, so some reference values are unavailable.
The source's latest date is shown. Monthly extremes require every daily value.

Irish station rain and archive rain both use **00:00–24:00 UTC** observing days.
The EDR service supplies recent hourly history, with gaps in some reports.
Existing same-station hours are saved between launches. Today/week totals show
measured subtotals and gaps. Month/YTD totals are unavailable until their full
hourly window is covered; eight days of rain are never presented as an entire
year. Rain-event totals and instantaneous rates are not supplied. No long crawl
runs at startup. Station pressure is labelled **station pressure**; it is not
silently treated as relative/mean-sea-level pressure. Dew point and feels-like
values are calculated from the measured fields and retain that attribution.

Radar remains RainViewer, with its own attribution and five-minute refresh;
the Irish location's official link opens Met Éireann. Coastal curves use the
Marine Institute's Irish National Tide Gauge Network, with metres **OD Malin
Head**, not the UK's Newlyn datum. Quality-unknown readings are marked provisional;
missing/bad readings are omitted. These are observed levels, not tide predictions.

The US edition remains separate in this release. One international app can use
this provider-by-location design, but each country's observations, forecasts,
warnings, climate, radar and tides need individually checked adapters and
licences. No universal national-government API or all-country support is claimed.

## Berkeley Building beacon rhyme

The complete rhyme is always visible beneath the building, with the line for
the current forecast highlighted:

> Steady blue, clear view.  
> Flashing blue, clouds due.  
> Steady red, rain ahead.  
> Flashing red, snow instead.

No line is highlighted when the forecast is unavailable. The highlight remains
visible when reduced motion is enabled, so the signal is readable without
blinking. The beacon continues to use the prevailing outlook over the next
twelve hours; snow includes wintry precipitation such as freezing rain.

## UK public stations must report measured rainfall

In the UK, every automatically selected public station, including explicit CWOP and
NOAA/airport modes, now needs a recent usable temperature **and a measured
precipitation amount**. The nearest qualifying CWOP station within 75 km is
preferred. Official fallbacks must meet the same requirement. An explicit dry
zero counts; missing, negative, non-finite, boolean, rejected or questioned rain
does not. Rain weather codes alone do not qualify. API precipitation reports are
the evidence used; the feeds do not provide a complete hardware inventory.

CWOP rainfall uses accepted MADIS per-sensor quality flags. US NWS precipitation
also requires an accepted quality flag and supported units. Airport METAR rain
amounts come from AWC's numerical 1/3/6/24-hour fields in inches, converted to mm.
Old saved temperature-only station choices are rejected at startup. If no
qualifying station can be found, the dashboard explains this and retries; it
does not fall back to a station without reported rainfall. Existing connected
personal Ambient stations retain their priority in Automatic mode.

The source caption shows the station's reported precipitation and its period.
Rolling totals are not converted to calendar-day, monthly or yearly counters.
A rain gauge does not guarantee that every calendar total is supplied by the
public feed. In the UK, the separately named Environment Agency gauge continues
supplying the calendar rainfall ledger where available; the selected weather
station's own reported rain remains separately labelled in the source caption.

## Start and update

1. Close the old Weather Desk console. Keep the old application folder as a backup.
2. Extract this download into a new folder. Copy the **private** folder from your
   existing **UK edition** into the new application folder to keep your
   saved connection, settings and history. Keep US and UK copies separate.
3. Double-click **START-WEATHER-DESK-UK.bat**. It prepares the included Python on the first run,
   checks the runtime, and opens **http://127.0.0.1:8766/**. Keep the console open.
4. On a fresh installation, choose your location in Settings. Leave readings on
   **Automatic**, or connect your Ambient station with its API key, application
   key and optional MAC. **Remember this station** saves the connection locally.

The bundled option is for **Windows 10/11, Intel/AMD x64**. No pip, Node or extra
Python packages are needed. If you prefer your existing Python 3.10+, use
**START-WITH-INSTALLED-PYTHON.bat**. These are explicit choices; the app prints
which Python it is using.

**CHECK-WEATHER-DESK-RUNTIME.bat** verifies bundled files and tests app imports,
TLS and SQLite. Keep its full output if startup fails. For an installed-Python
check, run `weather-desk.bat --installed-python --diagnose` in a terminal.

On macOS/Linux/Raspberry Pi, use Python 3.10+ and run:

```sh
python3 -I -X utf8 -B launch_weather.py --installed-python --open
```

The `.command` launcher does the same on macOS. Press Ctrl+C or close the console
to stop. Internet access is needed for weather feeds; saved history stays local.

## Startup implementation

The Windows shortcuts share **weather-desk.bat**. Its built-in PowerShell
command checks/extracts the included `runtime/python-windows-x64.zip` and verifies
all 39 files before Python starts. Later launches reuse those files. Preparation
does not access the network or change system PATH, registry or PowerShell policy.
`runtime-prepare.ps1` is the readable source of that one inline command.

**launch_weather.py** adds its own application directory to Python's import path
before loading any local module. For bundled Windows Python it registers the
private DLL directory before SSL/SQLite imports and retains the handle. Python's
upstream `_pth` file is now unchanged. Runtime checksums have one implementation
in the Windows preparation step; the former `windows_runtime.py` helper is gone.
Native binaries are unchanged from the official CPython 3.14.9 x64 distribution.
The archive retains the full upstream licence and a provenance record.

## Saved data and sharing

Everything personal stays in **private/**: credentials, preferences, observations
and caches. Windows encrypts remembered keys for your Windows account with DPAPI;
other systems use an owner-only local file. A session-only connection is not
saved. **Forget station** removes the saved connection.

Share the clean application ZIP with testers. Each person chooses their location
and keys. Keep your working private folder to yourself. The server uses a public
asset allowlist and binds to loopback by default. Images and copied weather text
include the source/time and omit keys and MAC addresses.

The page checks weather every minute and radar every five minutes. Feed-specific
caches prevent each page refresh from becoming a new upstream request. Completed
monthly rain counters and historical days remain saved after restarting.

## UK location and sources

Choose a UK postcode, town or 4-, 6- or 8-character Maidenhead grid. Europe/London
handles GMT/BST. The initial example is Swadlincote. Celsius and mm appear first,
with Fahrenheit/inches alongside; wind retains mph and km/h. Ambient account
keys work for stations connected to Ambient, not interchangeable Ecowitt/Davis
account services.

| Display | Provider |
| --- | --- |
| Personal readings | Ambient Weather API |
| Nearby CWOP | NOAA MADIS; named station, time and quality checks |
| Official public fallback | NOAA Aviation Weather Center global airport METAR |
| Beacon | Open-Meteo / DWD ICON seamless hourly numerical model |
| Daily climate/calendar | NOAA NCEI GHCN-Daily UK observations; calculated 1991–2020 references |
| Monthly archive | Met Office historic station files; calculated 1991–2020 averages |
| Public rainfall | Named Environment Agency Hydrology gauge in England |
| Radar | RainViewer past radar, bundled Natural Earth geography |
| Warning strip | Met Office point-filtered public warning polygons |
| Coastal tides | Environment Agency measured coastal levels; Admiralty link for predictions |

The beacon uses the same prevailing-12-hour clear/cloud/rain/snow rule as the US
edition, with the UK model named on screen. It is not a direct Met Office point
forecast. An unavailable forecast leaves the beacon unlit. Radar shows past
observations, not future rainfall. Maps/legends/timestamps refresh every five
minutes. Warnings retain their official wording and identify delayed/unavailable
feeds. Public API availability and coverage can change.

The daily climate reference is chosen from an audited UK station subset within
300 km. It may differ from the airport, rain gauge and monthly archive station.
Each is named. Raw daily archive files are saved locally and periodically
refreshed; the bundled Nottingham/Watnall seed prevents a large first-start
history download. Completeness and archive-through dates are shown.

Calculated daily normal values need at least 24 of 30 ordinary dates in 1991–2020,
or 7 of 8 leap days. Suspect daily values, missing precipitation and presumed
zero rain are excluded; trace rain stays flagged. Daily data can arrive late or
use the station's observing-day convention. The monthly Met Office table is
full-month context, distinct from the ledger's daily-archive to-date comparisons.

## Public rainfall

Airport METAR rain codes do not supply a measured rainfall amount. The public
rain ledger therefore uses one separately named **Environment Agency** gauge
within 30 km. Around Swadlincote the selected gauge can be **Overseal**. This feed
covers England; unsupported locations show unavailable rather than invented rain.
A connected Ambient station continues using its own counters.

All public rain totals use that gauge's measured 15-minute series. GMT source
timestamps are converted to Europe/London calendar boundaries: local midnight,
Monday week start, month start and January 1, allowing 23/25-hour DST days.
An event requires a known start after six complete dry hours. The 15-min rate is
the latest 15-min amount multiplied by four, not an instantaneous sensor rate.

The current-year gauge file is imported once in the background into
private/ea-rainfall.sqlite3; later starts reuse it and update recent intervals.
Refresh is 15 minutes. Missing/rejected intervals produce a measured lower bound
**≥** and coverage details. Incomplete or stale totals do not earn an exact
normal comparison. Unchecked provisional observations remain identified.
Missing historical intervals are not filled from a neighbouring station.

## MIDAS Open history

In **Climate archive → MIDAS history**, import yearly **qc-version-1 daily
temperature** and/or **daily rainfall** observation CSVs from CEDA, individually
or in a ZIP. CEDA requires a free account for downloads; the app does not need or
store that account's credentials. Import up to 300 files / 12 MB per batch.
Imported observations persist in private SQLite; re-imports avoid duplicates and
older releases do not replace newer rows.

MIDAS dates mean **24 hours ending 09:00 UTC on the displayed date**, including
summer. The importer does not shift the dates, combine 12-hour reports, infer
multi-day accumulations or blend them into NOAA calendar dates. Only supported
version-1 records are used. MESQL flags exclude suspect, estimated, accumulated
and unsupported values; retained corrections and traces are flagged. A trace
contributes zero to a sum while retaining the trace label. Incomplete periods
cannot become complete monthly or yearly totals.

The explorer shows the selected date, calculated 1991–2020 references when the
24/30-year (7/8 leap-day) criterion is met, prior-year extrema, complete monthly
summaries and January-to-selected-date rain. Extrema cover the imported files,
not every historical UK record. A temperature import does not supply a rain
baseline. Import observation files, not station-metadata CSVs. The daily NOAA
calendar, live readings and Met Office monthly table continue independently.

## Coastal tide chart

The coastal card plots the nearest reporting **Environment Agency tide gauge**,
with past 24 h of actual 15-minute mean levels. Port, distance, observation time
and datum are shown. It refreshes every 15 minutes and retains a dated cache.
Gaps over 30 minutes stay blank; readings older than 90 minutes are delayed.
Heights are **metres relative to Ordnance Datum Newlyn (m AOD)**, including normal
negative values. They are not Chart Datum heights or predictions of the next
high/low. Use the **Admiralty EasyTide** link for future tide times.

## Readings and comparisons

- Air temperature, feels like, dew point and humidity retain their existing
  calculations. Missing readings stay unavailable. A solar reading does not
  establish cloud cover; day/night colours use calculated solar altitude.
- The wind compass shows direction around its outer ring and speed in its centre.
  Beaufort is an equivalent of the reported speed. The formal scale's averaging
  and installation requirements can differ from a home station. Daily max gust
  uses a reported daily maximum; it is not inferred from a single gust.
- Pressure tendency compares an observation about three hours earlier, within
  30 minutes. A net change beyond ±0.5 hPa is rising/falling; otherwise steady.
  That neutral band is a display choice. QNH is labelled separately from sea-level
  pressure; different stations/datums are not combined into a trend.
- Ambient's annual rain counter is preferred. If missing, YTD adds each completed
  monthly station counter to the current month-to-date counter. Missing months
  are requested individually and saved. Missing data never contributes zero.
  The ledger's observed, normal and record windows all use the same cutoff date:
  month-to-date and **January 1 through today**. No June-to-June season is mixed in.
- The completed-day temperature mean is **(daily high + daily low) / 2**.
  The experimental calendar compares it with same-date 1991–2020 empirical
  thirds: red above the 66⅔ percentile, blue below the 33⅓ percentile, green
  between them (inclusive). Click a date for both cutoffs in °F/°C, the
  reference-year count, the observed mean, the normal/calculated average and the
  exact departure. This is an adaptation of
  [NOAA CPC's outlook categories](https://www.cpc.ncep.noaa.gov/products/predictions/erf_info.php?outlook=814&var=t),
  **not an official NOAA daily tolerance or a weather forecast**. For each
  reference station and month/day, paired highs and lows give one daily mean per
  year; the 1/3 and 2/3 quantiles use linear interpolation at `(n - 1) * p`
  (Hyndman–Fan type 7). Ties can narrow the band, and neighbouring dates are
  never substituted. At least 80% of the possible years need valid pairs: 24/30
  ordinarily, 7/8 for February 29 (a smaller sample, explicitly labelled). That
  threshold is our choice. Missing or accumulated observations, reversed
  high/low pairs and duplicate dates are excluded. Colours use unrounded values;
  the popup rounds for readability. Today, incomplete days and dates with
  insufficient historical coverage stay uncoloured, and a positive departure
  from normal can still be green, because a normal is a mean while the green
  range describes the historical distribution. The first calendar load reads
  the 30-year reference in the background and saves derived cutoffs in
  `private/temperature-calendar/terciles`, isolated by station and method;
  restarts reuse them and failed loads retry after five minutes. Colours from
  the old ±1°F method are never imported, and history is never filled with
  zeros. US records come from [RCC-ACIS](https://docs.rcc-acis.org/acisws/); the UK/Ireland edition reuses its named UK GHCN and Met Éireann climate archives, and its averages and thresholds remain explicitly calculated references.
- Climate references and live observation stations are named separately. Nearby
  sites and reporting-day conventions can differ. NOAA precipitation includes
  liquid water and melted snow; an unheated personal gauge can miss snowfall.
- **Past days** compares the selected historical date with the same climate date.
  A connected personal station's history is fetched at the normal API pace and
  saved. In public mode, historical daily observations come from the named
  climate archive, not a reconstruction from sparse live reports.

Automatic mode uses a connected Ambient station first. Otherwise it tries the
nearest usable **CWOP/APRSWXNET** report within 75 km via **NOAA MADIS**, then the
edition's official public provider. An explicit CWOP or official-provider choice
can override saved Ambient keys. Actual station, distance and observation time
are shown. Personal readings are stale after 15 minutes; public reports after
90 minutes. CWOP checks are cached for ten minutes with outage backoff.

MADIS quality checks apply independently to each sensor. Accepted descriptors
are C/S/V/G; rejected/questioned/bad or unchecked values stay unavailable. Rolling
24-hour rain is labelled separately and never added into calendar rain counters.
Public stations often omit solar, UV, daily max gust and monthly/yearly counters.
The MADIS guest route has no service guarantee; larger deployments should arrange
an appropriate distribution feed instead of increasing viewer polling.

## Radio & Space

The Radio & Space screen uses the available width: compact HF and reception
panels, a large propagation map, and solar imagery and NOAA measurement plots.

**NOAA SWPC** supplies SFI, daily sunspot number, planetary A, Kp, observed
geomagnetic scale, solar-wind speed, Bz/Bt, GOES X-ray class, R/S/G scales,
bulletins and D-layer absorption. Daily values show their observation date; they
commonly describe the previous UTC day. Missing or stale values remain labelled,
never converted to zero or an all-clear. **KC2G** supplies the MUF/grey-line and
foF2/NVIS maps. MUF is for 3000 km hops; foF2 is a critical frequency for
near-vertical paths. D-RAP shows absorption, not a continuous LUF. Map
timestamps and legends remain intact.

- **HF forecasts repaired:** KC2G's live response has 25 columns. The previous
  parser demanded exactly 24 and rejected every band. The 24 UTC positions are
  now retained, with model-generation checks. Errors appear next to the table
  instead of being hidden inside the model notes.
- **No grid required for a listed destination:** 340 current DXCC entities and
  511 regional locations are searchable by name or prefix. DXCC locations are
  representative points; choose a city for a more specific path. Only the
  Custom Maidenhead choice requires a locator. Your TX location is taken from
  saved weather-station settings; the dashboard calculates its grid for you.
- **Two automated prediction models:** KC2G uses its current ionospheric model
  for circuit predictions on 10/12/15/17/20/30/40/60/80/160 m. VOACAP, via
  HamClock.com's compatible service, gives typical monthly planning
  probabilities. Select the model above the table or in Path setup. Context and
  caches are kept separate by station, destination, mode, power and provider.
  KC2G recalculates at most every 15 minutes per path; VOACAP once per hour.
  Restarting can reuse fresh saved predictions.
- **KC2G assumptions:** path setup saves destination, mode, power, antenna gains
  and receiving-noise environment. Reliability columns cover now, +3 h and +6 h;
  SNR describes the first displayed hour. SSB assumptions are 3000 Hz/6 dB, CW
  500 Hz/0 dB and FT8 50 Hz/−3 dB. The lowest usable band is the lowest tested
  frequency reaching 50% predicted circuit reliability, not a measured
  continuous lowest usable frequency.
- **VOACAP assumptions:** this backend requires whole-watt power (1–1500 W);
  fractional watts remain available in KC2G. The nine-band protocol covers
  80, 60, 40, 30, 20, 17, 15, 12 and 10 m, with fixed 0 dBi isotropic antennas
  and a 3° minimum takeoff angle. Backend noise and mode SNR thresholds apply.
  The service returns reliability, not SNR, and does not provide 160 m; those
  fields stay blank with an explanation. KC2G's gain and noise controls are
  disabled for VOACAP. Neither model guarantees a contact. Green/amber/red band
  cells mean ≥75%, 50–74% and <50% modelled reliability; grey means unavailable.
  These display bands are separate from NOAA's storm-severity colours.
- **NOAA colours:** level 0 green, 1 yellow, 2 gold, 3 orange, 4 red, 5 dark red.
  Current R/S/G, 24-hour maximums, forecast probabilities and forecast G are
  separate. Missing or stale data is grey. Kp colours follow the G thresholds;
  X-ray colours follow the corresponding instantaneous flare thresholds. SFI,
  sunspots, solar wind and Bz keep their physical units rather than inventing
  NOAA severity scales for them. The Kp forecast says partial when few forecast
  intervals are available.
- **NOAA imagery and plots:** switch between SUVI 195 Å, northern aurora and
  CCOR-1 corona imagery (five-minute checks; original image times retained).
  X-ray and proton plots use six hours of actual NOAA observations; Kp shows the
  past 24 hours. Charts are plotted locally from NOAA JSON data, keep gaps, use
  logarithmic axes for flux and show sample values on pointer hover. Unavailable
  images or data have a clear status and an official product link.
- **PSK Reporter:** recent digital reception reports for your station callsign,
  or an override in Path setup. Shows receiving stations, band/mode and time,
  with receiver grid, frequency and reported SNR in the tooltip. At most 100
  reports from the last hour are requested. Queries are paced across callsigns
  to at least five minutes, and the pacing survives restarts. This reads
  reports; it does not transmit or submit spots. No reports does not mean a
  band is closed, and digital reception is not proof of an SSB opening.

The tab checks locally once a minute while open. Source caches range from two
minutes to an hour; KC2G maps refresh at 15 minutes and D-RAP at five minutes.
Small public-feed caches and path preferences persist in private/radio/. The
selected HF provider receives approximate TX/RX coordinates and station model
settings; PSK Reporter receives only the requested callsign. Ambient credentials
and weather-station identifiers are never sent to radio providers. VOACAP
Online's website is not scraped: its full controls open separately, with
copyable generated grids for the same path. The automatic table uses a
HamClock-compatible backend. For a self-hosted compatible service, optionally
set `WEATHER_DESK_VOACAP_URL` to its HTTPS endpoint, or HTTP on localhost. Use
community services for personal reference and respect their availability.

Sources: [NOAA SWPC](https://www.spaceweather.gov/),
[KC2G](https://prop.kc2g.com/hfprop/planner) ([acknowledgments](https://prop.kc2g.com/acknowledgments/)),
[NOAA D-RAP](https://www.spaceweather.gov/products/d-region-absorption-predictions-d-rap),
[HamClock.com](https://www.hamclock.com/credits.html) ([terms](https://www.hamclock.com/terms.html)),
[HamClock backend protocol](https://github.com/openhamclock/hamclock-standards),
[PSK Reporter developer documentation](https://pskreporter.info/pskdev.html),
[AD1C Country Files](https://www.country-files.com/cty/cty.dat) and the
[VOACAP regional-location catalogue](https://www.voacap.com/hf/).
Destination facts were retrieved November 8, 2026 UTC. No third-party app or
propagation engine code is bundled.

## Maintenance and verification

This cleanup consolidates startup and fixes isolated-Python app imports. Unused
US public-weather/alert providers, the US-only location resolver and its copied
reference tests have been removed from this edition; shared climate mathematics
and the active UK providers remain. Obsolete styling, repeated state resets,
unused imports and old setup notes have also been removed. Source Python and
JavaScript are formatted for maintenance. Saved private data formats are retained.

See **VALIDATION.md**. The Linux build environment cannot exercise a native
Windows launch. MIDAS tests use synthetic format fixtures; real account-protected
CEDA downloads require that account access.

## Sources and licences

- Python: https://docs.python.org/3.14/using/windows.html ; full notices inside the runtime archive.
- Airport METAR: https://aviationweather.gov/data/api/
- MADIS/CWOP: https://madis.ncep.noaa.gov/madis_cwop.shtml ; https://madis.ncep.noaa.gov/madis_sfc_qc.shtml
- Daily archive: https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily
- Met Office station files: https://www.metoffice.gov.uk/research/climate/maps-and-data/historic-station-data
- MIDAS temperature: https://doi.org/10.5285/1854bb17ec454841b04e243a1352f25a
- MIDAS rainfall: https://doi.org/10.5285/88b5bdf6131e4c1cb7acb8bde984deda
- MIDAS QC: https://data.ceda.ac.uk/badc/ukmo-midas/metadata/doc/QC_J_flags.html
- EA rainfall: https://environment.data.gov.uk/hydrology/doc/reference
- EA tide gauges: https://environment.data.gov.uk/flood-monitoring/doc/tidegauge
- Forecast: https://open-meteo.com/ ; model data under CC BY 4.0.
- Radar: https://www.rainviewer.com/ ; geography: https://www.naturalearthdata.com/
- Warnings: https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings ; see data/NSWWS-Open-Licence.pdf.
- Future tides: https://easytide.admiralty.co.uk/

Met Office MIDAS Open and EA data: Open Government Licence v3.0. Contains
Environment Agency information © Environment Agency and/or database right.
No MIDAS observations or CEDA account credentials are bundled.

## Free satellite lightning

The UK and Ireland edition uses EUMETSAT’s Meteosat Lightning Imager. Its free Data Store downloads use your EUMETSAT account’s consumer key and secret.

Sun and Moon share one card, preserving all previous sky readings. The
Lightning nearby card uses official satellite flash positions and UTC times.
Distance (mi/km) and bearing are calculated from your saved dashboard location.
The closest accepted flash is selected within 75 km, over a 45-minute
observation window **ending at the displayed satellite data time**. Satellite
flashes can be inside clouds and do not establish exact ground-strike points.

NOAA GOES-19 supplies eastern locations and GOES-18 western locations, selected
at 105°W. The US adapter's supported area is within 52° north/south and between
180°W and 45°W. Areas outside that support remain map-only. NOAA's catalogue
is checked every minute; data older than five minutes are marked delayed.
The first load downloads a full observation window (typically tens of MB),
with progress visible. Later polls download new 20-second granules and reuse
old files; restarts reuse the local raw-file cache.

UK/Ireland uses EUMETSAT LI Level-2 Lightning Flashes, EO:EUM:DAT:0691, via
the Data Store's ten-minute archive body files. In Settings, enter a free
account's consumer key and secret from https://api.eumetsat.int/api-key/ .
These are separate from Ambient keys and may be saved with the same Windows
account encryption or private-file protection. OAuth bearer tokens remain in
server memory and renew automatically; no credentials reach the browser or
external map. The catalogue is checked every five minutes. Actual observation
time is shown; files older than 30 minutes are marked delayed. There is no
paid lightning subscription. Old Weatherbit credentials, if present in a copied
private folder, are ignored; no Weatherbit calls are made.

All NetCDF processing uses a locally bundled h5wasm worker, version 0.10.3.
No extra Python packages, DLLs, CDN scripts or changes to the bundled runtime
are needed. CF scale/offset, fill values, UTC units and quality flags are read.
NOAA quality flag zero is accepted. EUMETSAT flash confidence above 0.5 is
excluded (zero is highest confidence); missing/geolocation/radiometric input
warnings keep empty results provisional. Files with missing columns or invalid
arrays fail visibly. A partial window says nearest loaded flash. No detections
requires all window files decoded, complete catalogue coverage and no quality
warnings, with fresh data. Failures and old observations remain explicit.

Raw satellite files are cached privately, with a 160 MiB/three-hour retention
target. The server limits simultaneous downloads to three, accepts only
catalogue-registered IDs and bounds each file to 32 MiB. It never fetches
arbitrary URLs supplied by the browser.

The Blitzortung map remains a separately attributed live embed; its activity
can differ from the satellite observations. Its restricted raw feed is never
scraped. Third-party notices are in third-party/h5wasm-LICENSE.txt.

Sources: https://www.ncei.noaa.gov/products/goes-terrestrial-weather-abi-glm
and https://user.eumetsat.int/resources/user-guides/mtg-li-level-2-data-guide .
Contains modified EUMETSAT Meteosat data, 2026, under CC BY 4.0; see
https://www.eumetsat.int/legal-framework/data-policy .

## Nearby personal stations and recalled pressure

Settings → More nearby personal stations accepts an optional **Weather
Underground data API key**, separately from an Ambient key or a WU station
upload key. Use your own appropriately entitled WU account. Public observation
mode checks the closest qualifying WU station against qualifying CWOP stations
within 75 km. A connected Ambient station retains priority in Automatic mode;
Official mode does not query either personal-station network. In Ireland,
Automatic retains Met Éireann; choose Nearby personal station to use this pool.

WU location metadata and observations must pass the provider's QC flag.
Current readings must be no more than 60 minutes old and include a usable
measured daily precipitation total. Zero is valid; missing rain is not zero.
The directory returns up to ten candidates and is cached for one hour;
observations are checked every ten minutes. A provider failure retains other
available sources. A WU key does not prove station calibration or exposure.
No cross-network identity match is assumed from a similar name or location.

CWOP now requests an earlier, QC-accepted pressure observation from MADIS when
its local history cannot supply the three-hour comparison. The request is
relative to the current observation's timestamp, and accepts a point within
30 minutes of the target. Station ID, coordinates and pressure type must
match; QNH and sea-level pressure are not mixed. Current weather remains
visible while the history request runs. Archive failures preserve current
readings, collect local history and retry after 30 minutes. Cached comparisons
survive app restarts. No personal weather station connection is necessary.

WU can retrieve its selected station's recent rapid history with the same
key. Interval pressure ranges are labelled approximate (≈); if the range
crosses a direction threshold, the tendency is labelled uncertain. Saved
instantaneous observations take priority over interval summaries. A missing
history response is not replaced by another station's pressure.

Today's WU rain is the station's midnight-to-current counter. It is never
relabelled as a rolling 24-hour, monthly or yearly amount. The UK/Ireland
calendar rain ledger continues using its separate named official rain gauge.
Complete monthly/YTD WU rain reconstruction is not part of this integration.
Saved WU keys use the app's existing private credential store (Windows DPAPI
on Windows). Keys are not returned by APIs or included in cards and exports.
Forgetting the key disables WU selection and invalidates pending results.

Official API references:
- https://www.wunderground.com/pws-api/utility/search/near
- https://www.wunderground.com/pws-api/observations/current-conditions
- https://developer.weather.com/docs/openapi/pws-recent-history-1-day-rapid-2-0
- https://madis.ncep.noaa.gov/madis_cwop.shtml

## Twilight, time since rain, and ten-minute Beaufort

The Sun & Moon card now shows the current sky stage: Daylight, Civil twilight,
Nautical twilight, Astronomical twilight, or Night. Twilight also says dawn or
dusk. Stages use the calculated geometric solar altitude at the saved location
and the current time: apparent sunrise/set at approximately −0.833°, then
−6°, −12° and −18°. Cloud cover does not change the astronomical stage. The
calculation works when a weather station is disconnected and at polar locations.

The rainfall heading shows elapsed 24-hour days since recorded rain. A connected
Ambient station uses its reported lastRain timestamp when available. A native
rain timestamp may include seconds while an Ambient observation is rounded to
the minute. Public UK/Ireland ledgers use their same named EA or Met Éireann
rain gauge and validated interval history. Other public sources retain observed
wet bounds and continuous dry coverage per station in the private folder.
Any positive measured amount counts at that source's reporting resolution;
forecast/radar rain and missing observations do not count as measured rainfall.

An approximate sign means the last rain was bounded by a reporting interval.
A ≥ sign means only a minimum dry period is supported; earlier rainfall or gaps
prevent an exact date. The counter ends at its observation/gauge coverage time,
so it does not accumulate unobserved dry days while a source is offline. Hover
or focus the rain-age badge for its source, last wet time and coverage. Cached
station choices and source changes never share rain histories.

Beaufort now uses a ten-minute scalar mean, excluding gusts, with no correction
for sensor height. The compass continues showing the latest reported wind.
Ambient's windspdmph_avg10m is preferred. Weather Underground's documented
current ten-minute mean and UK/Ireland METAR ten-minute convention are used
where supplied. The Met Éireann hourly wind_speed_mean is a one-hour mean;
its Beaufort input is separately calculated from ten complete, consecutive
one-minute wind_speed means at the selected station. A missing/rejected minute
prevents that calculation. Its source and window end are retained.

Where there is no native ten-minute mean, a time-weighted scalar estimate may
be calculated from at least three actual reports covering the whole ten-minute
window, with no gap over 5½ minutes. It is explicitly labelled an estimate.
CWOP requests a small 20-minute history only around the selected station, with
per-variable QC and station/location checks. A station reporting every fifteen
minutes cannot establish a ten-minute mean: Beaufort stays unavailable rather
than using its instantaneous wind or gust. Screen, copied report and PNG use
the same mean-based Beaufort calculation.

References:
- https://aa.usno.navy.mil/faq/RST_defs
- https://www.met.ie/forecasts/marine-inland-lakes/beaufort-scale
- https://ambientweather.com/support/aw-misc/
- https://www.caa.co.uk/publication/download/12602
- https://opendata2.met.ie/edr/collections/observations-swob-nrt-1min?f=json
- https://opendata2.met.ie/edr/collections/observations-swob-nrt-60min?f=json

## Network access (optional)

By default the dashboard listens on this computer only. To open it from a phone or
tablet on your home network, start it with `START-WEATHER-DESK-UK-LAN.bat` (or add `--lan`). The console
lists addresses such as `http://192.168.1.50:8766/`. There is no password, so use
this only on a network you trust, and allow Python through Windows Firewall on
Private networks only. `--allow-host NAME` adds an extra computer name.

If you use your own installed Python 3.10+, start `START-WITH-INSTALLED-PYTHON-LAN.bat`
instead, which does the same with that Python.
