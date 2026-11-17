# Bundled dashboard assets

The dashboard embeds vendored JavaScript, CSS and font files so rendering does not require a CDN connection. `dashboard_assets/sources.json` records upstream source URLs; the release manifest pins the distributed bytes. Preserve the bundled license files and upstream notices when redistributing.

| Asset | License notice |
| --- | --- |
| Tailwind CSS runtime | `dashboard_assets/tailwind-LICENSE.txt` (MIT) |
| Chart.js | `dashboard_assets/chart-LICENSE.md` (MIT) |
| Marked | `dashboard_assets/marked-LICENSE.md` (MIT) |
| DOMPurify | `dashboard_assets/purify-LICENSE.txt` (Apache-2.0 OR MPL-2.0) |
| Font Awesome Free CSS/fonts | `dashboard_assets/fontawesome-LICENSE.txt` (code MIT; fonts SIL OFL 1.1; icons CC BY 4.0) |

The project's own source code is MIT-licensed; this does not replace upstream asset licenses. The Tailwind browser runtime is retained for compatibility with the existing dashboard; a future production build can replace it with compiled CSS without changing plan/data semantics.

FIT encoding follows Garmin's [workout file specification](https://developer.garmin.com/fit/articles/file-types/workout.html). CI installs the official `@garmin/fitsdk` decoder pinned to **21.221.0** in a temporary test directory to independently verify signatures, CRCs, units, targets and decoded messages. The SDK is a test dependency only; it is not bundled into the dashboard or required at runtime. See the [Garmin FIT SDK](https://developer.garmin.com/fit/get-the-sdk/) for its source and license. Physical watch transfer/execution is a separate, unverified step.

## Race maps and terrain services

The Home Assistant wrapper bundles Leaflet 1.9.4 under BSD-2-Clause, with its upstream licence and source record beside the assets. [Leaflet licence](https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE). The standalone source does not bundle Leaflet. Map tiles remain an external OpenStreetMap service; preserve visible [OpenStreetMap contributor attribution](https://www.openstreetmap.org/copyright), normal browser caching and the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

Automatic missing-height lookup uses the [Open-Meteo elevation API](https://open-meteo.com/en/docs/elevation-api), based on Copernicus DEM 2021 GLO-90. Returned terrain data is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); preserve both Open-Meteo and Copernicus credit, the licence link and notice of derived sampling/interpolation. This service's [free endpoint terms](https://open-meteo.com/en/terms) restrict use to noncommercial applications, independently of the data licence and this project's MIT source licence. The app uses the API; it does not bundle the Open-Meteo server. [Race-planning privacy and precision limits](RACE_PLANNING.md#map-and-elevation-profile) describe location sharing and approximate surface heights.
