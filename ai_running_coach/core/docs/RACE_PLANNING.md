# Race planning and Garmin course exports

The Home Assistant **Race planning** tab is for running and trail-running races. Upload a GPX course, review its measured distance and elevation, and ask the coach for two strategies. Further questions refine the saved race plan without changing the training calendar.

- **Conservative:** a controlled approach based on the available athlete evidence and course demands.
- **Ambitious:** the same conservative opening, with an optional second-half pickup checkpoint and explicit conditions for keeping the conservative approach instead.

These are coaching estimates, not a physiological prediction. The AI sees recent running observations, longer outings, separate cross-sport load and current athlete reports. It must explain uncertainty. Missing or stale distance/duration evidence produces effort-only sections; a time estimate is not invented to enable an export.

## Course data

GPX files may be up to 5 MiB and 50,000 track/route points. Parsing and downloads are local and require no AI request. Raw GPS points stay in the private installation; the AI receives a bounded course summary rather than the coordinates. GPX names and imported observations are treated as data, not instructions. Do not post your private course or athlete database in a support issue.

Distances follow the uploaded polyline. Disconnected segments are retained and flagged, with gaps excluded from the measured distance. Upload a continuous route before requesting pacing or exporting a FIT course. The app does not invent a straight connection through missing terrain.

Elevation is optional. Missing heights are unknown, not zero. Partial elevation makes total ascent and descent unavailable. Known contiguous height spans use 25 m distance sampling, five-sample median smoothing and a 5 m vertical deadband to reduce the accumulation of GPS noise. Small hills may be lost; totals and grades are approximate. No terrain service, weather service or automatic altitude correction is called.

## Requests and saved plans

A race review uses the same normal daily request allowance as training reviews. Each explicit request permits at most one provider attempt, without automatic retry or model fallback. Reading the tab, uploading a GPX, downloading a course and reusing unchanged completed evidence do not call the model. A failed or invalid response consumes its attempted request and preserves the previous valid race plan. Follow-up context is limited to the preceding saved plan, rather than an unlimited conversation.

## Forerunner 965 and PacePro

**Course FIT is a PacePro starting point, not a native PacePro strategy file.** Garmin's public FIT documentation describes course records and optional target speeds, but this project does not claim an undocumented PacePro encoding.

The untimed course contains the route and any supplied heights. Its FIT timestamps establish point order only; a watch might still derive a pace from them, so do not treat that as coaching guidance. Create a PacePro plan with your explicitly chosen goal. If a complete numeric strategy is selected, a timed course can include midpoint section times and speeds for a Virtual Partner-style pacing reference. That midpoint is an export representation of the AI's estimated range, not a guarantee or a new fitness calculation. Effort-only strategies remain available as coaching guidance; they do not get fabricated target speeds.

On a Forerunner 965, Garmin documents loading a course and choosing **Navigation → Courses → [course] → PacePro → Create New**, then entering a goal time or pace. Use the reviewed strategy and its uncertainty to choose that goal. Garmin Connect also provides PacePro creation from a running/trail course, with adjustable pacing and uphill effort. Automatic upload or deletion is not provided.

Official references:

- [Garmin FIT course file structure](https://developer.garmin.com/fit/file-types/course/)
- [Forerunner 965 PacePro instructions](https://www8.garmin.com/manuals/webhelp/GUID-0221611A-992D-495E-8DED-1DD448F7A066/EN-US/GUID-27B26831-3708-46EA-BF15-18039D28EC3A.html)

FIT exports are checked with the official Garmin decoder. Physical watch transfer and execution still require testing on the device.
