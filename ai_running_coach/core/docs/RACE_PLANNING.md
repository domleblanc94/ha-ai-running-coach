# Race planning and Garmin course exports

The Home Assistant **Race planning** tab is for running and trail-running races. Upload a GPX course, review its measured distance and elevation, and ask the coach for two strategies. Further questions refine the saved race plan without changing the training calendar.

- **Conservative:** a controlled approach based on the available athlete evidence and course demands.
- **Ambitious:** the same conservative opening, with an optional second-half pickup checkpoint and explicit conditions for keeping the conservative approach instead.

These are coaching estimates, not a physiological prediction. The AI sees recent running observations, longer outings, separate cross-sport load and current athlete reports. It must explain uncertainty. Missing or stale distance/duration evidence produces effort-only sections; a time estimate is not invented to enable an export.

## Course data

GPX files may be up to 5 MiB and 50,000 track/route points. Uploading, inspecting and downloading a course use no AI request. The app retains the original upload and its parsed course separately from the active course with any added terrain heights. Invalid uploads leave the previous course unchanged. The AI receives a bounded course summary rather than raw coordinates. GPX names and imported observations are treated as data, not instructions. Do not post your private course or athlete database in a support issue.

Distances follow the uploaded polyline. Disconnected segments are retained and flagged, with gaps excluded from the measured distance. Upload a continuous route before requesting pacing or exporting a FIT course. The app does not invent a straight connection through missing terrain.

### Map and elevation profile

The Race planning tab normally displays an interactive OpenStreetMap background with your course drawn locally. The browser fetches only visible map tiles from OpenStreetMap; tile requests disclose your IP address and the area being viewed, not the GPX file or athlete profile. Requests send only the site origin as their referrer, excluding the private Home Assistant ingress path. Attribution stays visible. Maps require internet access; the local route outline and elevation chart remain available when tiles cannot load. The app does not download offline map packs or prefetch an entire route. See the [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/) and [privacy policy](https://osmfoundation.org/wiki/Privacy_Policy).

The elevation profile is part of every import. Valid heights already in the GPX are preserved. If heights are missing, the app automatically requests terrain samples from **Open-Meteo**, which uses the **Copernicus DEM 2021 GLO-90** digital surface model at approximately 90 m resolution. A fully elevated GPX makes no terrain request. The upload screen explains this before you submit a file. Sampled locations are sent to Open-Meteo; its API logs may retain coordinates for up to 90 days. This is separate from AI use and does not consume the coaching allowance. No Garmin request is needed. See the [elevation API](https://open-meteo.com/en/docs/elevation-api) and [service terms/privacy](https://open-meteo.com/en/terms).

The no-key terrain service is for **personal, noncommercial use**; running your own personal home installation is within its stated scope. Commercial deployments must arrange a permitted terrain service before using automatic lookup. The returned data is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Open-Meteo and Copernicus attribution, source information and an indication of sampling/interpolation accompany enriched course data and exports. This data licence is separate from the project's MIT source-code licence. See [Open-Meteo's licence](https://open-meteo.com/en/licence).

Lookup is bounded to 500 sampled locations and five requests per attempt, with a local private cache and no automatic retry. Missing heights are interpolated only inside supported spans of the same segment; GPX gaps and unavailable terrain remain unknown. If the service fails, the imported course stays available and the profile explains its incomplete coverage. Use **Retry missing elevation** after connectivity recovers. Previously saved courses can use the same retry action; opening an old course alone does not send a new terrain request. Updating course evidence makes older pacing results inapplicable until a new explicit coaching review is requested.

Terrain heights estimate the surface, which may include trees and buildings; they are not surveyed trail elevations or a correction to recorded GPX heights. Partial elevation makes total ascent and descent unavailable. Known contiguous height spans use 25 m distance sampling, five-sample median smoothing and a 5 m vertical deadband to reduce accumulated noise. This processing does not improve the terrain model's underlying resolution. Small hills may be lost; totals and grades are approximate. No weather service is called.

## Requests and saved plans

A race review uses the same normal daily request allowance as training reviews. Each explicit request permits at most one provider attempt, without automatic retry or model fallback. Reading the tab, uploading a GPX, downloading a course and reusing unchanged completed evidence do not call the model. A failed or invalid response consumes its attempted request and preserves the previous valid race plan. Follow-up context is limited to the preceding saved plan, rather than an unlimited conversation.

## Forerunner 965 and PacePro

**Course FIT is a PacePro starting point, not a native PacePro strategy file.** Garmin's public FIT documentation describes course records and optional target speeds, but this project does not claim an undocumented PacePro encoding.

Enriched GPX downloads embed terrain attribution and derivation notes in their metadata. The FIT format used here does not embed those notices: download the adjacent **Elevation source notes** file and retain it with any shared FIT course. The **Download original GPX** download preserves your upload; older imports made before original-file retention do not have that download.

The untimed course contains the active route, supplied heights and any saved terrain estimates. Its FIT timestamps establish point order only; a watch might still derive a pace from them, so do not treat that as coaching guidance. Create a PacePro plan with your explicitly chosen goal. If a complete numeric strategy is selected, a timed course can include midpoint section times and speeds for a Virtual Partner-style pacing reference. That midpoint is an export representation of the AI's estimated range, not a guarantee or a new fitness calculation. Effort-only strategies remain available as coaching guidance; they do not get fabricated target speeds.

On a Forerunner 965, Garmin documents loading a course and choosing **Navigation → Courses → [course] → PacePro → Create New**, then entering a goal time or pace. Use the reviewed strategy and its uncertainty to choose that goal. Garmin Connect also provides PacePro creation from a running/trail course, with adjustable pacing and uphill effort. Automatic upload or deletion is not provided.

Official references:

- [Garmin FIT course file structure](https://developer.garmin.com/fit/file-types/course/)
- [Forerunner 965 PacePro instructions](https://www8.garmin.com/manuals/webhelp/GUID-0221611A-992D-495E-8DED-1DD448F7A066/EN-US/GUID-27B26831-3708-46EA-BF15-18039D28EC3A.html)

FIT exports are checked with the official Garmin decoder. Physical watch transfer and execution still require testing on the device.
