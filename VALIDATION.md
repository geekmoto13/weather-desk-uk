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
