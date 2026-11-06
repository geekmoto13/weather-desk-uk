# Validation — version 1.2

- Version 1.2 re-applies the optional home-network (LAN) mode to the current build. No other functional change.
- Default start is unchanged (127.0.0.1 only). With --lan, private-network IP addresses and this computer's name are accepted; other host names and public IPs are still refused (checked over HTTP).
- Existing Python tests pass.

## Previous release validation

# Validation — UK15

- 339 Python tests and 84 JavaScript tests passed, including the existing
  station, rainfall, climate and launcher regression suites. Python 3.10 syntax
  and modified JavaScript syntax passed.
- All allowlisted assets and HTML IDs passed local HTTP checks.
- The release package is CRC/hash checked and startup is checked with isolated
  installed Python from an unrelated working directory. The existing bundled
  runtime and launchers are unchanged. Native Windows runtime and visual browser
  testing remain unverified in this Linux session.
- No credentials, personal settings, downloaded weather reports or QA captures
  are included in this release.

## Previous release validation

# Validation — UK14

- 325 Python and 78 JavaScript checks pass. New tests cover native
  ten-minute means versus live wind/gust, real calm zero, scalar time weighting,
  interpolation and window clipping, insufficient samples and long gaps,
  twilight stages through a complete day, dawn/dusk and polar daylight/night.
- Rain-age tests cover native timestamps and minute-rounded observations,
  missing/future/naive values, continuous interval coverage, lower bounds,
  gaps, offline periods, rolling-window constraints, counter changes and resets,
  per-station isolation, saved restart history and history arriving after the
  first live request. No missing report becomes a dry zero.
- UK/Ireland tests also cover ten consecutive one-minute means, filtered live
  API response shape, missing/invalid/rejected minutes, station identity,
  truncated responses, conflicting duplicate readings and rejection of the
  hourly averaging period for the ten-minute Beaufort input.
- Live NOAA MADIS recwin=4 retrieval returned real multiple CWOP observations.
  The tested F1308 sample reported at 15-minute intervals; it correctly cannot
  provide a ten-minute estimate. A synthetic three-report history passed the
  complete HTTP-to-JavaScript pipeline and produced a 10 mph mean / Beaufort 3
  while its latest wind was 20 mph.
- A real Met Éireann one-minute wind response for Valentia (0102), ending
  4 November 2026 at 21:00 UTC, produced 3.2700548794 mph from ten complete
  minute means. The hourly metadata explicitly describes a one-hour mean;
  the minute metadata describes the preceding minute. A data request for a
  later unavailable window returned no observations and remained unavailable.
- Both local HTTP servers pass all page asset and HTML-ID checks, native
  Ambient wind/rain handling, rain-cache restart, public station/gauge source
  isolation and CWOP-history-to-Beaufort calculation. These route tests use
  labelled fixtures, not private user station credentials.
- Python 3.10 syntax and changed JavaScript syntax are checked. Closed ZIPs
  are CRC/hash checked, extracted and started twice using the isolated
  installed-Python launcher, with saved-setting restart persistence. Runtime
  archives, bootstrap files and launchers remain byte-identical to the input
  release. Native Windows and visual browser execution remain unverified.
- No private keys, settings, rain caches, development scripts, research feed
  captures or QA output are included in the download. Earlier release archives
  are retained separately. Production code remains standard-library Python
  and locally bundled JavaScript with no added dependency downloads.

## Previous release validation

# Validation — UK13

- 299 Python and 67 JavaScript checks passed in this update.
  New coverage includes CWOP pressure-only archive records, matching station
  and datum, observation-relative UTC queries, visible current readings during
  recall, cache migration, restart reuse and persistent failed-history backoff.
- Optional WU coverage includes documented nearby/current/history endpoints,
  metric conversions, passed QC, fresh observations, required daily measured
  rain, skipped closer stations without rain, provider fallback in both
  directions, saved/session/forgotten keys, safe auth/network errors, refused
  redirects, late results after a key change and cross-provider history isolation.
  Pressure interval summaries carry approximation/uncertainty rather than a
  fabricated exact tendency. New form-controller tests check save, forget,
  cleared password fields and recoverable errors.
- A live MADIS lookup for F1308 retrieved QNH of 1000.5 hPa at 23:15 UTC on
  26 October and 998.7 hPa at 02:15 UTC on 27 October 2026: falling 1.8 hPa
  over 3.0 hours. The comparison survived a fresh provider instance using its
  saved cache, with no additional request.
- Both shipped local HTTP servers passed checks for all page assets and HTML
  IDs, source selection, WU and CWOP pressure recall, saved-key restart,
  forgetting keys, key-free status responses, private-file isolation and
  CSRF/origin rejection. These used explicitly labelled API fixtures, including
  a Gresley fixture; they are not evidence of that station's live API eligibility.
- No WU data API key was provided. Live authenticated WU requests remain
  unverified. There are no borrowed website keys, scraped station feeds or
  new production dependencies. Python 3.10 syntax parsing and JS syntax pass.
- UK airport fallback also queries earlier observations for its selected airport
  when needed, without relaxing fresh-temperature/rain selection. Station,
  location and pressure datum boundaries and archive failure are tested.
- The release ZIP is CRC/hash checked, extracted and bootstrapped twice from an
  unrelated working directory with installed Python. Bundled Windows runtime
  bytes and launchers are unchanged. Native Windows and visual browser testing
  remain unverified in this Linux session.
- Keys, private folders, cached observations, local research captures and QA
  tools are excluded from the download. Previous released archives are retained
  independently of the working folders.

## Previous release validation

# Validation — UK12

- 270 Python and 63 JavaScript checks passed. They cover
  official NOAA and EUMETSAT catalogue parsing, strict product/body selection,
  complete/gapped/short/reprocessed windows, New Year times, observation-time
  freshness, account-free NOAA, saved/session EUMETSAT credentials, token
  renewal, safe auth errors, credential generation changes, cache reuse,
  unknown file IDs, unreadable binary downloads and redirect privacy.
- Decoder cases check packed CF values, scalar/array attributes, UTC origins,
  missing and quality flags, valid empty arrays, mismatched arrays, EUMETSAT's
  confidence scale and input warnings, distances/bearings, nearest versus
  newest, repeated flashes, and completeness. UI cases check partial/stale
  results, no false empty results, map continuity, late workers after location
  changes and clearing consumer credentials after connecting.
- A full live NOAA run downloaded and decoded all 135 NetCDF files in one
  complete 45-minute window. It used the exact shipped browser IIFE and worker
  in a JS VM against the actual local HTTP routes. The Lynn window contained
  no accepted nearby flashes. A separate live-file positive case decoded
  562 total flashes and found three within 75 km of a reported flash location.
- EUMETSAT's live collection EO:EUM:DAT:0691 catalogue was verified, including
  ten-minute BODY entry URLs. OAuth, authenticated download, key persistence
  and the exact worker pipeline were checked using a labelled synthetic
  NetCDF fixture following the official LI specification. Packed coordinates,
  UTC occurrence time, confidence rejection and provisional incomplete
  coverage worked. No live EUMETSAT account credentials were available, so
  authenticated live EUMETSAT downloads remain unverified.
- Local HTTP checks passed for both editions: all assets, WebAssembly/worker
  policy, CSRF/origin protection, private-data isolation, known-ID binary
  downloads, disk-cache reuse, saved settings/history and restart persistence.
- Python 3.10 grammar, changed-file static analysis, JavaScript syntax, unique
  HTML IDs and asset references pass. The previous Weatherbit key field is
  replaced with EUMETSAT fields; other controls are retained. No visual browser
  check is claimed; executing a worker in a VM is not a visual browser test.
- The closed ZIP is atomically installed, CRC/hash checked, extracted and
  started twice through the isolated installed-Python launcher from an
  unrelated working directory. The Windows runtime archive, manifests,
  preparer and bootstrap remain byte-identical to UK11; native Windows
  execution remains unverified in this Linux session.
- h5wasm 0.10.3 is pinned, integrity verified, bundled locally with its upstream
  licence and notice. There are no added native Python/DLL dependencies and
  no CDN imports. Recent raw files are cached with a 160 MiB/three-hour target.
- No user keys, private folders, real settings/history, development tools,
  sample feed captures or QA-generated NetCDF files are included in the ZIP.
