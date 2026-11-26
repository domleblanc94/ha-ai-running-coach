# AI Multisport Coach

**Version 1.5.0** is the community release of AI Multisport Coach for Home Assistant. It serves one athlete and opens its setup and private dashboard through Home Assistant ingress.

## Choose a route or preview the dashboard

- **Home Assistant app:** use Home Assistant OS on amd64 or aarch64. Follow the [repository installation instructions](https://github.com/domleblanc94/ha-ai-running-coach#install), start the app and select **Open Web UI**. The app manages its own daily scheduler.
- **Standalone Python:** use Windows, macOS or Linux with Python 3.11+ and a terminal. Follow the [standalone setup guide](https://github.com/domleblanc94/ai-running-coach/blob/main/docs/STANDALONE.md) in the [standalone repository](https://github.com/domleblanc94/ai-running-coach). Home Assistant is optional; you manage scheduling and dashboard hosting.
- **Preview first:** open the [synthetic live demo](https://domleblanc94.github.io/ai-running-coach/) with a fictitious athlete and activities. It makes no AI or Garmin requests.

The two installations have separate private profiles, databases, plans and request ledgers. Moving between them requires a planned migration; see [Moving from the standalone installation](#moving-from-the-standalone-installation).

Review AI-generated training and check provider costs before enabling scheduled reviews. FIT export and additional provider-account compatibility retain the limitations described below.

## Add to the Home Assistant sidebar

In Home Assistant, go to **Settings → Apps → AI Multisport Coach → Info** and turn on **Show in sidebar**. The menu entry is named **AI Multisport Coach** and uses the running icon. It is available to Home Assistant administrators. You can turn it off on the same page.

If you installed from the documented repository URL, you can jump to the app page. [Open AI Multisport Coach in Home Assistant](https://my.home-assistant.io/redirect/supervisor_app/?app=ce7ba695_ai_running_coach)

## First setup

1. Create your athlete profile and set your training mode and availability. Choose running, cycling, swimming or triathlon; existing profiles remain running until you change them. Use A for your main goal and B or C for supporting goals, with a sport for each goal. Each goal can have an optional date and target. A race can also have ideal, main and fallback outcomes for the same event. With no target or outcome, the race goal defaults to **Just finish**. Existing goals remain running goals when you change modes; select their sports deliberately. Choose your heart-rate zone mode in the same form.
2. Choose your activity source. Offline mode skips Garmin sync and starts with the profile as its evidence. For Garmin mode, obtain your own official Personal API key and review the permitted use of your data. Configure the key and account region in the app. [Garmin developer program](https://developer.garmin.com/gc-developer-program/overview/)
3. Select a model provider and enter its key. Generating or reviewing a plan uses that provider, including with the offline activity source. The default allowance is two coaching requests per UTC day, shared by manual and scheduled runs. Check your provider's account limits and pricing separately.
4. Save your IANA timezone (for example, Europe/London) with scheduling unchecked. This controls plan dates and the daily review time. Request counts follow UTC days.
5. Generate and review the first plan. Enable a local review time when you are ready for scheduled reviews. The app keeps the last saved dashboard if a review fails. The scheduler runs one review per local day. If it fails, use **Run now** after checking the cause. Manual runs still use the core request budget. A run interrupted by a restart pauses the schedule until you re-enable it.

Provider integrations use a common request budget. Gemini remains the primary live-tested route; compatibility of other providers and models with a particular account requires verification and remains experimental. Offline transport tests do not guarantee every model supports the same request options.

Local summary import is currently available through the [standalone project](https://github.com/domleblanc94/ai-running-coach/blob/main/docs/STANDALONE.md). Use the Home Assistant app logs to diagnose startup failures. Keep credentials, athlete data and model response snapshots out of support reports.

## Activity review detail

In **Setup & settings → Athlete**, choose **Coaching detail**: Concise, Standard or Detailed. Detailed asks for more interpretation of the recorded activity, useful coaching comments, uncertainty and practical next steps. It applies to the next successful review; saved historical comments are not rewritten just by changing the setting. Saving the preference makes no model request, and the existing request/output limits remain in effect.

New interval and strides sessions must explain warm-up, work repetitions and duration or distance, effort, recovery and cooldown. Existing incomplete sessions stay saved until a valid coaching update replaces them. A rejected incomplete response retains your last valid plan and shows a specific reason in Coach Status.

## Desktop MCP activity details

The optional desktop MCP companion can request full details for a saved activity by its activity ID. It returns the stored activity metrics, source provenance and units, plus paginated splits. Sensor streams are included only when requested and are paginated separately. Missing measurements remain unknown; the response identifies missing streams and laps that are not stored. It does not fetch new Garmin data or invent missing records.

Enable the private connection and create its dedicated token through Home Assistant ingress, then configure the desktop companion using the [MCP setup guide](https://github.com/domleblanc94/ai-running-coach/blob/main/docs/CHAT_WITH_COACH.md). The LAN port is unassigned and the connection is disabled by default. The same token and source-policy checks protect detail requests. Shared details exclude raw GPS coordinates, credentials and private connection tokens.

Reading details makes no AI request, uses no coach request allowance and does not change your profile or saved plan. The companion can retrieve further pages when a long activity has more splits or samples. Details reflect the records already saved in the app; they do not guarantee that every measurement available from a device has been imported.

## Race planning

Open **Race planning** and upload a running or trail-race GPX (up to 5 MB and 50,000 points). The map displays OpenStreetMap tiles with your route drawn locally, and every import includes an elevation profile. Supplied valid GPX heights are preserved; missing heights automatically start an Open-Meteo terrain lookup. Your original upload is retained. Uploading, terrain lookup and course downloads use no AI or Garmin request and leave the training plan unchanged.

The elevation chart labels distance along the course in **kilometres (km)** and height in **metres (m)**, with numeric axis ticks. Missing-height spans and disconnected sections remain gaps; chart labels do not alter the course data.

Before Upload, the page explains that OpenStreetMap receives map-tile requests for the area you view and Open-Meteo receives sampled route locations when heights are missing. The free Open-Meteo service is for personal, noncommercial use; its API logs may retain coordinates for up to 90 days. It uses Copernicus GLO-90 approximate 90 m surface heights, which may include trees and buildings. Terrain data is CC BY 4.0 and source/derivation notices accompany enriched courses. Full-height GPX files do not contact the elevation service. The browser sends the map service only your site origin as referrer, excluding the private ingress path.

If terrain lookup fails, the valid imported course remains saved and the profile reports incomplete coverage; select **Retry missing elevation** when ready. An older saved course with missing heights can also be retried. Opening it alone does not make an elevation request. The local outline/profile still work if map tiles cannot load. Unknown heights stay unknown, disconnected tracks must be corrected before pacing or FIT export, and all elevation totals are approximate. Changing course evidence requires a new explicit race review before using an older strategy with the updated course. [Provider terms, privacy, attribution and precision details](https://github.com/domleblanc94/ai-running-coach/blob/main/docs/RACE_PLANNING.md#map-and-elevation-profile).

Enter the race name, optional date and what you want to discuss. **Generate race strategies** makes at most one model request using the shared daily allowance and your saved athlete/activity evidence. It does not sync Garmin or change the training calendar. The coach compares a conservative approach with a more ambitious option whose later pickup depends on conditions. If evidence is too weak for numerical pacing, the plan provides effort guidance instead. Read the dated evidence and limitations alongside any finish-time range.

After missing-height lookup finishes, select **Generate race strategies** to have both conservative and ambitious options use the current course elevations, section gradients and stated terrain limitations. Importing or enriching the route does not automatically call the coach or rewrite previous advice. A changed course identity does not reuse an older strategy; if you already generated the strategies after elevation was added, you do not need to regenerate them just for this chart update.

Change your question and select **Refine strategies** to discuss the saved race plan. Each new review uses the same allowance as training reviews; an identical completed request can reuse its cached result without another model call. Failed requests retain the previous valid race plan and do not retry automatically. Only one coaching action runs at a time. The full GPX is retained in private app storage. The AI receives compact route/terrain summaries instead of coordinates; the separate missing-height service receives the sampled locations disclosed above.

**Course GPX** and **Course FIT starter** downloads do not call a model. Enriched GPX embeds terrain-source metadata. FIT does not embed those notices, so retain the adjacent **Elevation source notes** download with any FIT you share. **Download original GPX** preserves the literal upload for new imports; old imports without the original file retain their parsed course. A strategy with supported numerical timing also offers a timed course FIT reference. These are course files, not native PacePro plans. A timed course cannot detect how you feel or switch to the ambitious strategy automatically.

On a Forerunner 965, load the course through your Garmin workflow, choose an outdoor running activity, then **Navigation → Courses → your course → PacePro → Create New**, and enter your chosen goal time or pace. Garmin creates its own splits, which may differ from the coach's estimates. Follow [Garmin's PacePro instructions](https://www8.garmin.com/manuals/webhelp/GUID-0221611A-992D-495E-8DED-1DD448F7A066/EN-US/GUID-27B26831-3708-46EA-BF15-18039D28EC3A.html). Course files have offline decoder checks; physical watch transfer/execution remains unverified.

## Coaching request allowance

In **Setup & settings**, set **Daily AI request limit** from **1 to 20** requests. The default is **2**. Saving a different allowance does not start a coaching run or clear the requests already recorded today. Each app run still makes at most one coaching request; raising the daily limit does not add automatic retries. Coach Status shows **AI requests today** and the next reset at **00:00 UTC**, which may differ from midnight in your selected timezone. Failed provider attempts count towards the allowance; offline previews and reading the dashboard do not.

When the normal allowance is spent, **Run once anyway** lets you explicitly confirm one extra coaching request for that UTC day. Choose **Review current plan** or **Generate new plan**; with no saved plan, the action generates the first plan. The extra request leaves the normal allowance unchanged. It is spent when the AI request is reserved in the saved ledger, including an attempt that is interrupted or fails. A sync or preflight failure before that reservation, or reuse of a cached review without a new request, leaves the extra available for another explicit confirmation. There is no automatic retry. Restarting the app does not restore a spent extra, and a confirmation for a previous UTC day cannot spend a request on the new day. Scheduled runs cannot use this exception.

The allowance is an app limit, not a monetary spending cap. Increasing it or confirming an extra request does not override Gemini/Google or any other provider's quota, rate limits, account permissions or pricing. If the provider itself reports a quota error, wait for its reset or review the provider account before requesting more work.

## Profile, training days and downloads

After initial setup, the app opens on **Training dashboard**. Use **Athlete profile** to review your saved goals, available training days, training background and sport-specific settings. Use **Setup & settings** when you want to edit them.

The calendar keeps dates in their Monday-to-Sunday columns, including a first week that starts partway through the week. **No session planned** means the saved plan has no entry for that date; it is distinct from a prescribed rest day. Multiple sessions on one date appear together. Compact mobile layouts omit empty gaps. Updating the app corrects the display of an existing dashboard without requiring a new coaching request or changing its saved session dates.

Save changes to available training days before generating a replacement plan. With no days selected, availability is unspecified and the coach chooses the days. Once days are selected, a new plan must keep future training within them. Rest days may fall on any day, and an actual configured race can remain on its booked date. A saved profile change does not rewrite the existing plan by itself: the coach must return a valid new plan before it replaces the upcoming schedule. Completed and historical sessions remain part of your training record.

Use **Preferred long-run days (optional)** to offer the coach one or more suitable days. Select several to offer alternatives; this does not ask for a long run on every selected day. Use **No preference** to clear the selection and let the coach choose from your available days. If you have selected available training days, preferred long-run days must be among them. Save the profile before requesting the next review or replacement plan.

The app supports one planned running session per date. Different sports, or a run and a strength session, may share a date. If the coach moves a run to a date that already has a run, it must reconcile the existing session in the same update. A conflicting response is rejected and the last valid saved plan is retained. Conflicts already present in an older plan are shown for review; saving settings or updating the app does not silently move or delete those sessions.

Download the calendar as a PDF for printing or sharing. You can also download **athlete.md** and **training_plan.md**. These exports contain your private profile and training information; store and share them accordingly. They do not contain connection API keys. Exporting saved data does not make an AI request.

## Training phases

The coach can label the plan with phases such as **Baseline building**, **Build**, **Peak**, **Taper** and **Recovery**, using names and dates appropriate to your goals. Each phase has inclusive start/end dates and a short purpose. Python validates and displays those labels; it does not infer phases from workout types or race dates.

Expand **Training phases** in the dashboard to see the saved progression. **Current phase** refers to the dashboard's displayed date. **Phases in this week** follows the selected calendar week, showing each exact date range when a phase changes midweek. Uncovered dates show **Phase not specified**. The PDF calendar includes these weekly ranges and a separate overview with full phase purposes.

Upgrading does not rewrite existing plans or use an AI request. Existing plans remain unspecified until the next successful review supplies phase labels; the last dashboard remains available if the review fails. Phase updates use the normal coaching request and budget, with historical labels retained. The private **training_plan.json** is authoritative: Markdown, HTML and PDF are views of its saved metadata, and editing exported Markdown does not assign or change phases.

## Running, cycling, swimming and triathlon

**Setup & settings** offers a **Training focus** and optional availability for each sport. Sport days narrow the global training days; leave them blank to inherit the global setting. Preferred long-run and long-ride days are alternatives, not requests for several long sessions. Keep optional cycling FTP, swimming CSS, pool size and sport-specific HR ranges blank when unknown. Running ranges are not reused as cycling or swimming ranges. **Learn HR zones** applies to running baseline observations; it is not a cycling or swimming test protocol.

One coach review considers all enabled disciplines and their combined demands. The same daily request allowance and output limit apply; enabling triathlon does not add per-sport calls. The dashboard labels and filters disciplines, keeps distances and pace measures separate, and shows combined recorded timer duration with missing-data coverage. Composite Garmin activities without separate imported legs remain composite training time, not invented per-sport distances. The PDF includes sport labels and linked brick order.

Swimming distinguishes pool and open water. Enter the actual pool length and choose metres or yards; 25 yards is stored as 22.86 metres. CSS is an optional measured pace per 100 metres. Blank values stay unknown. Save profile changes before a new coaching review; saving or upgrading does not rewrite the current plan. The app retains the last successful dashboard HTML during an update. New sport charts and session labels appear after the next successful coaching run renders a dashboard; setup/profile controls are available immediately.

## Download workouts for your watch

Running, cycling and explicitly configured pool-swimming sessions can be previewed and downloaded as FIT workouts directly in the browser. Running supports HR/pace targets; cycling adds explicit Watts and cadence targets, without guessing FTP conversions. Pool workouts support stroke targets and time/distance/open steps. Missing targets remain open. The pool setting must agree with the profile; missing or mismatched settings show an explanation instead of a download.

The preview displays pool distances in the selected m/yd unit while preserving metres internally. Prescriptions that are not whole pool lengths remain unchanged and show an informational notice. Garmin documents the Forerunner 965's [updated pool-workout behavior](https://support.garmin.com/en-CA/?faq=fXDWMUWcuw5ACMmLqjjC59); inspect the resulting watch steps and alerts before relying on them.

Open-water swims and combined multisport/race entries are plan-only. Linked brick legs download as individual sport files with their order in the filename. Start each leg separately; no automatic transition or combined watch file is promised. The exporter allows up to 50 explicit steps per file. Edited or incomplete workouts use the existing review confirmation; preview changes affect only the download, not the saved plan.

Files require manual transfer. This app does not upload to Garmin Connect, sync or delete watch workouts. The HA mobile app may require an external browser for downloading. Offline tests use Garmin's official FIT decoder, but physical Forerunner 965 transfer/execution remains untested. See the [standalone transfer guide](https://github.com/domleblanc94/ai-running-coach#download-a-workout-for-a-forerunner-965).

## Learn HR zones from baseline runs

Choose **Learn HR zones from baseline runs** in the athlete profile to ask the coach to include conversational baseline runs in the normal training plan. After Garmin sync, use **Baseline run observations** to select one of up to 12 eligible runs from the past 21 days. Add your perceived effort, talk-test result and sensor type. The coach can review those observations during the next normal review under the existing request budget.

Learn mode preserves any saved ranges. If you have no saved ranges, they stay unknown. If you have ranges you want to use, switch to **Enter configured ranges** and save them yourself. Baseline check-ins are stored privately and can form part of the coaching context sent to your selected provider.

## Troubleshoot a failed run

Start with **Coach Status** in the app. A failed run shows its time, stage, reason code and recorded model request count when available. Home Assistant also shows a short diagnostic under **Settings → Apps → AI Multisport Coach → Log**. After upgrading from an earlier release, check Coach Status before retrying. The app can recover a more specific explanation from records already saved for that run. If those records are incomplete, the reason can still be unknown.

- **Garmin or activity sync:** check the selected activity source and Garmin access. If Garmin is limiting requests, wait before trying again.
- **Provider authentication or request:** check the selected provider and model in Settings, and confirm the key and model access with that provider.
- **Daily budget:** wait until 00:00 UTC, change the normal allowance in **Setup & settings**, or confirm **Run once anyway** if today's extra request is still available.
- **Provider quota:** check the provider account or wait for its reset. The app's extra request cannot bypass a provider quota.
- **Truncated or invalid response:** the app keeps the last valid plan. Check the reason and request allowance before retrying.
- **Replacement instructions or session identifier conflict:** the AI response could not safely replace the upcoming plan. The saved plan stays available. Updating may explain an earlier failure more precisely, but it does not replay rejected responses or start another request.
- **Training-day conflict:** the proposed plan used a day outside your saved availability and was not applied. Review your saved days, then request a new plan when your allowance permits. Any previous plan stays visible until it is replaced successfully.
- **Rendering, publication or storage:** check the app logs and storage. Back up the app before reinstalling or restoring it.

For support, share the app version, failure time, stage, reason code and recorded model request count. If the code points to the provider, include its name and the model identifier. Keep keys, raw provider responses, prompts, profiles and activity records private.

If the app has no saved plan yet, select **Generate new plan**, or **Plan baseline runs** in learn mode. **Run now** is available after the first plan is saved.

## Data and backups

The app stores its data under `/data/app` in Supervisor's private app volume. Home Assistant controls access through ingress. The profile, activity summaries, notes and other coaching context can be sent to the configured model provider during reviews. Check its data handling terms before enabling live reviews.

Include this app in a Home Assistant backup before updating. The Supervisor stops the app while taking its backup, allowing SQLite state to be saved consistently. Protect backups as you would the original data and keys. Restore the app with its data and verify the account binding, request history and dashboard before resuming a schedule.

## Moving from the standalone installation

Version 1.3 has no guided migration from a standalone installation. The existing installation and this app have independent databases, Garmin binding and request ledgers. Keep the app schedule disabled until you have verified the migration.

A future cutover needs a consistent source backup and one active scheduler. The migration needs to preserve the athlete profile and plans, SQLite request history for daily limits, and the exact Garmin account binding and source provenance. Copying only the athlete profile, or starting a new database against the same account on the same day, loses that continuity. Verify the restored files and review state with the schedule disabled. Keep the original installation and backup available for rollback. Stop the app schedule before resuming the standalone scheduler.

## Garmin key replacement

Garmin account binding is preserved if a key changes. Version 1.3 has no in-app confirmation for key rotation. Back up the app and pause its schedule before rotating a key. Ask the maintainer for the same-account recovery procedure, and keep the sync checkpoint with its matching database. An account or region change requires a new workspace.

FIT workout export remains experimental and requires manual transfer to a compatible watch.
