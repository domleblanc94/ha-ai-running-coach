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
