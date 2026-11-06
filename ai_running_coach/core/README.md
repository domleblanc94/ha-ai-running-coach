# AI Coach

**Public beta 0.2.3b5** for technical self-hosters running their own installation. Expect to use a terminal, edit a private `.env` and profile, and review generated plans. This is a source-checkout application, with optional Home Assistant publishing. It is not a hosted service or a turnkey watch-sync app.

A self-hosted running coach that combines AI-authored training plans with local activity analysis and a mobile-friendly HTML dashboard. Run it manually or on a schedule; optionally publish the page to Home Assistant using your existing SSH transport.

The AI decides training progression, recovery, intensity and plan adjustments. Python calculates and compresses activity evidence, validates returned changes, stores versioned plans and renders the dashboard. It does not substitute a fixed training policy for the model's decisions.

| Area | Beta support and evidence |
| --- | --- |
| Local setup, imports, plan validation/recovery and dashboard | Offline regression tests and clean-source setup checks; a personal Linux installation also exercises the daily workflow. |
| Official Garmin Personal API | Mocked protocol/error tests plus one personal installation's real sync and historical import; other accounts remain unverified. |
| Gemini coaching | New installs select `gemini-3.5-flash-lite`. One live synthetic response passed validation, saved-plan application and HTML rendering; real-athlete coaching quality remains unverified. Existing configured models are preserved. |
| OpenAI, Anthropic and custom compatible endpoints | Experimental adapters with mocked protocol tests; live account/model compatibility is not established by those tests. |
| FIT workout downloads | Experimental. Offline encoding/decoder checks passed; physical Forerunner 965 transfer/execution remains untested. Files need manual transfer; no automatic watch upload or deletion. |
| Mobile browser / HA use | Static dashboard with an optional status channel; download behavior varies by browser. No authenticated feedback form or watch companion. |

See the [release review](docs/RELEASE_REVIEW.md) for the validation scope. Report problems through [the public repository](https://github.com/domleblanc94/ai-running-coach) with the version, Python/OS, provider/model and sanitized error; do not attach keys, real databases, profiles or request snapshots.

## What it does

- Imports your activities through Garmin's official Personal API, or accepts local summaries in offline mode.
- Generates a new training block or adapts an existing one using your goals, availability, zones, history and dated feedback.
- Applies valid future-session changes automatically; preserves historical sessions and the last-good plan on failures.
- Shows upcoming workouts, weekly planned/recorded distance, rolling running summaries, long-run history, coach priorities and uncertainty.
- Supports Gemini, OpenAI and Anthropic through an explicitly selected provider. There is no automatic switch between providers.
- Tracks actual model attempts and usage, caches identical completed evidence, and limits automatic request spending.
- Keeps local check-ins and activity notes available for subsequent reviews.

## First try: no account or API call

Python 3.11 or newer is required. Download/extract the source release, open a terminal in its top-level folder, and create a virtual environment:

```sh
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python setup_coach.py --demo demo
python -m http.server 8000 --bind 127.0.0.1 --directory demo
```

Open `http://127.0.0.1:8000/dashboard.html`. The banner, activities, zones and sessions are synthetic examples; this demo is not a personalized plan. The demo refuses to overwrite a nonempty directory. A missing live health sidecar is shown as unknown rather than healthy.

## Set up your own coach

```sh
python setup_coach.py
```

Choose an AI provider and activity source. Setup creates `.env`, an empty SQLite database, an import folder and an editable `athlete.md` with explicitly unknown HR zones; existing files are preserved. Edit your profile with availability, experience, current injury status and goals. Add your own configured HR zones if known; otherwise leave them explicitly unconfigured for effort-based coaching. No zones or race dates are inferred. Add an event using this exact format:

```markdown
**A-Race:** Your event name
**Date:** 2027-05-15
**Target Time:** Your goal or completion
```

For configured zones use fields such as `**Zone 2 (Endurance):** YOUR_LOWER - YOUR_UPPER bpm`, replacing placeholders with your own values and describing how/when they were obtained. Do not substitute example numbers. Check the profile locally with `python setup_coach.py --doctor`. This checks profile/database/schema, dependencies, selected provider/model, required key presence and enabled publishing configuration without printing secrets or contacting services. Missing live credentials produce actionable errors; `--doctor --offline` checks local readiness without requiring a key. It does not verify physiological accuracy or whether a key actually works.

The Gemini, OpenAI and Anthropic adapters use the installed HTTP client directly; no provider SDK is required. Optional extras below remain convenient installation aliases:

```sh
python -m pip install ".[gemini]"
# Alternatives: ".[openai]" or ".[anthropic]"
# Official Garmin uses the same HTTP dependency.
```

This is a source-checkout application: keep the scripts, assets and examples together in the extracted folder. Run commands from that folder; installing optional dependencies does not relocate your data. Put the provider key in `.env` and choose an accessible model identifier. Example identifiers are starting values, not promises of account access, free-tier eligibility or price. Provider terms, model availability and costs can change.

The new-install Gemini default is `gemini-3.5-flash-lite`, listed as stable with a free tier in Google's [model documentation](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite) and [pricing](https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite) when checked on November 4, 2026. Free access depends on your account and limits; paid accounts follow Google's pricing. Setup does not rewrite an existing `.env`. If you explicitly use deprecated `gemini-2.5-flash`, `--doctor` reports Google's access/routing notice; choose a replacement yourself after reviewing the [migration guidance](https://aistudio.google.com/docs/whats-new-gemini-3.6#migrate-to-gemini-3-5-flash-lite).

Before your first live plan request, inspect the inputs without a model call:

```sh
python setup_coach.py --doctor
python ai_coach.py --dry-run --new-plan
```

Check that your goals, availability, activity source and missing-data labels are correct. Review `.env` request/output limits before enabling a schedule. The live `--new-plan` command below automatically saves a valid AI-authored future block; inspect that block before training from it. The request-count limit is not a monetary spending cap.

In `athlete.md`, an optional line such as `**Preferred Long Run Days:** Saturday, Sunday` gives the coach alternative long-run days. It does not request a long run on every listed day. Leave it blank or use `No preference` to let the coach choose. When weekly availability is explicit, preferred days must be within it. New plan changes must keep at most one running session per date; a run and a strength session can share a date. Existing conflicts need a valid coach update, rather than silent changes to saved sessions.

| Setting | Purpose |
| --- | --- |
| `COACH_PROVIDER` | `gemini`, `openai` or `anthropic` |
| `COACH_MODELS` | Ordered model identifiers within that provider; no cross-provider fallback |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Key for the selected provider |
| `OPENAI_BASE_URL` | Optional explicitly chosen OpenAI-compatible endpoint; review its data handling |
| `COACH_ALLOW_KEYLESS` | Explicitly permit keyless access to a custom OpenAI-compatible endpoint; default false |
| `COACH_HTTP_TIMEOUT_SECONDS` | Bounded provider request timeout, default 60 seconds |
| `COACH_MAX_OUTPUT_TOKENS` | Output limit, default 4096; 8192 may help an initial longer block |
| `COACH_MAX_ATTEMPTS` | Maximum attempts per direct coaching invocation, at most two; the scheduled pipeline uses at most one |
| `COACH_MAX_DAILY_REQUESTS` | Normal coaching request allowance per UTC day, default 2; configurable from 1 to 20 |
| `COACH_ACTIVITY_SOURCE` | `garmin` for the official Personal API, or `offline` for local/manual imports |
| `GARMIN_PERSONAL_API_KEY` | Your official Garmin Personal API key; never a Garmin account password |
| `GARMIN_PERSONAL_REGION` | Official endpoint region, `global` (default) or `china` |
| `GARMIN_BACKFILL_DAYS` | Initial import window in days, default 56; daily refresh covers the recent 14 days |
| `GARMIN_MAX_REQUESTS_PER_RUN` | Bounded HTTP requests per sync, default 100; not a claim about Garmin account quotas |
| `GARMIN_HTTP_TIMEOUT_SECONDS` | Per-request timeout, default 30 seconds |
| `COACH_PUBLISH_ENABLED` | `false` for local use; `true` to require configured publication |

Choose `garmin` only after obtaining your own official [Garmin Personal API](https://developer.garmin.com/gc-developer-program/overview/) access and reviewing its terms. Set `GARMIN_PERSONAL_API_KEY` in `.env`. The adapter exchanges the key for a short-lived token and caches it privately in `.garmin_personal_token.json`; this project does not ask for your Garmin login/password. This is personal access for your own data, not a multi-user commercial Garmin integration. Account access and permitted downstream AI processing remain your responsibility. Automated tests use mocked Garmin responses; a separate personal-installation smoke check verified official sync on Linux. That does not establish compatibility or permissions for other accounts.

Daily sync refreshes the last 14 days; initial imports use a configurable 56-day window and resume an unfinished window on subsequent runs. The request ceiling bounds each run. A 429 response respects the returned retry interval and leaves the run incomplete rather than publishing partial evidence as fresh.

Preserve the private sync checkpoint/account binding with your database. A changed key is not silently treated as a new account: for a replacement key you know belongs to the same account, explicitly use `python garmin_sync.py --accept-key-rotation`. Another account/region or a lost account binding requires a fresh workspace. `--refresh-token` renews only the private access-token cache, without fetching activities.

Choose `offline` to use local CSV/JSON data without an activity API. Never treat a change of file format as permission to process data you are not entitled to use. Existing installations with legacy source histories should follow the documented fresh-workspace migration rather than relabelling old records as official Garmin data. The public source no longer includes the previous activity API connector or unofficial Garmin client.

```sh
# Official Garmin users first download their permitted activity summaries:
python garmin_sync.py
# Then ingest; offline/local users can skip the preceding sync line.
python database_manager.py
# Uses a live provider call and automatically applies a valid new plan:
python ai_coach.py --new-plan "Build a training block around my profile and availability"
# Inspect inputs without calling a model or changing state:
python ai_coach.py --dry-run
python generate_dashboard.py
```

For your own dashboard, copy only `dashboard.html` and `coach-status.json` into a separate access-controlled web output directory, or use the optional uploader below. Never serve the project folder containing `.env`, profiles and SQLite data. To refine an existing block, run `python ai_coach.py` or `python ai_coach.py --build-plan`. To start a replacement future block, use `--new-plan` with your request: prior future sessions are superseded only after valid replacement output. Past sessions remain recorded. `python ai_coach.py --update-profile` starts a provider conversation and saves only when you explicitly enter `save`; you can also edit the Markdown directly.

## Move an existing installation to a fresh official-Garmin workspace

```sh
python setup_coach.py --fresh-garmin-workspace ../my-garmin-coach
```

The destination must be new/empty and outside the current checkout. Only public code/assets/examples are copied. A fresh official-Garmin database, blank credentials and editable profile are initialized; old activity history, AI reviews, plans, check-ins and credentials are **not** copied or relabelled. Your original workspace stays intact for backup/reference. Re-enter only the profile facts you have approved, configure the official Garmin key and selected model in the new `.env`, then perform your first sync and request a new plan from the fresh history. Change cron paths only after checking the new workspace. This is deliberately separate from an in-place data migration. Coaching refuses legacy/unknown or unofficial source histories instead of silently sending them to a model under the new official source name.

```sh
cd ../my-garmin-coach
python setup_coach.py --doctor
python garmin_sync.py --backfill-days 56
python database_manager.py
python ai_coach.py --new-plan "Build a plan from my profile and official Garmin history"
```

## Import all available official Garmin history

After a successful initial current-data sync and ingestion, run an isolated historical backfill:

```sh
python garmin_sync.py --historical-backfill --start-date earliest --end-date YYYY-MM-DD
```

Set the end date to the intended coverage date. `earliest` discovers the oldest available official activity without assuming a ten-year limit or reading archived measurements. Historical progress is saved separately in `.garmin_personal_history.json`; it does not change the daily current-data checkpoint or claim a fresh daily review. Each invocation keeps the existing HTTP-request ceiling. Repeat the same command with pacing to resume committed progress until the exact window is marked complete; completed payloads are ingested within the historical workflow. Respect a service-provided retry interval instead of increasing caps or repeatedly retrying a 429. A completed window becomes a no-op. No model call is made by backfill. Run an independent available-ID verification afterward:

```sh
python garmin_sync.py --historical-backfill --start-date earliest --end-date YYYY-MM-DD --verify-history
```

This scan omits the lower date bound, checks all available top-level Garmin activity IDs through the end date, repairs missing IDs and confirms coverage only after ingestion. It does not try to match another provider's count. Repeat the same verification command if its bounded run needs to resume. Child legs of composite activities are excluded by the documented top-level search mode.

After completion, a normal `python garmin_sync.py` followed by `python database_manager.py` verifies current incremental operation. Source counts need not equal an archived provider's count: only Garmin's available records are imported, never relabelled copies of legacy measurements or AI reviews. An archive may contain older activities that Garmin does not make available; those remain preserved in the archive and are not converted.

## Local activity files

Import CSV or a JSON list that you are entitled to use independently. This does not convert restricted API data into permitted AI data. `examples/activities.csv` and `.json` are fictional schema examples, not a universal watch/export format. Convert your own permitted export to the documented columns first; FIT/GPX parsing is not included.

```sh
python import_activities.py --file /path/to/activities.csv --db ai_coach_master.sqlite --dataset my-watch --dry-run
python import_activities.py --file /path/to/activities.csv --db ai_coach_master.sqlite --dataset my-watch
```

Required fields: `id` (positive integer), `start_date` (ISO date/timestamp), `type`. Optional fields: `name`, `distance_m`, `moving_time_s`, `elapsed_time_s`, `elevation_gain_m`, `avg_heartrate`, `max_heartrate`, `perceived_exertion`, `description`. Units are metres, seconds and bpm. Empty/null values stay unknown. Stable IDs plus a dataset name allow repeated imports without creating duplicates. Existing databases are backed up before writes. Import does not call a model; a later explicit coaching review sends included evidence to the configured provider.

## Feedback that reaches the coach

Add dated observations to an activity description, for example:

```text
[coach: rpe=4; fatigue=2; pain=none; recovery=normal; fueling=2 gels; fueling_tolerance=good]
```

RPE is 1-10; fatigue is 0-10. All fields are optional. `carbs_g` and `fluid_ml` are session totals, not hourly rates. Missing quantities stay unknown. Source refreshes bring recent edited descriptions into subsequent evidence. A separate durable rest-day check-in needs no activity or external service:

```sh
python coach_checkin.py --date 2027-03-01 --recovery "Felt recovered" --comment "Optional context"
```

The dashboard's checkboxes are browser-only reminders. They do not tell the AI a workout was completed and do not submit feedback.

## Download a workout for a Forerunner 965

Running sessions have a **Download workout** button in the upcoming list and calendar. It opens an editable preview; nothing is sent to a server. Review the compact step list and expand any row to edit step order, watch instructions, warm-up/recovery, time or distance limits, lap-button/open endings, and optional HR/pace ranges. Running workouts are limited to 50 steps. Changing units/target mode clears incompatible values rather than reinterpreting a number. Ambiguous legacy sessions and edited workouts require a review confirmation; an unchanged structured workout downloads directly after preview. The result is a `.fit` workout. Browser edits affect that file only, not the saved coaching plan.

Structured coach-authored steps are preserved when available. Legacy prose starts with a conservative editable step, not an invented interval recipe. For “4 km including 4 x 30-second strides”, the four strides are part of the stated total: the exporter does not append them to an extra 4 km, invent recovery times, infer stride pace, or convert timed strides into distance. Explicit lap-button steps are an option when timing/placement is intentionally flexible. For an HR ceiling without a known lower bound, keep the numeric watch target open rather than inventing a range; the original guidance remains visible for review.

The [Forerunner 965 manual](https://www8.garmin.com/manuals/webhelp/GUID-0221611A-992D-495E-8DED-1DD448F7A066/EN-US/Forerunner_965_OM_EN-US.pdf) documents structured workouts and USB/MTP access. This feature downloads a file; it does not send it to Garmin Connect or the watch. Arbitrary workout-FIT import through the Connect mobile app is not claimed. The usual third-party FIT workflow is a USB transfer to the watch's `GARMIN/NewFiles` directory (for example via Windows MTP), followed by disconnecting and checking the watch workout list. This path is described by [Garmin workout tooling](https://github.com/mkuthan/garmin-workouts); actual Forerunner 965 hardware transfer/execution has not been tested here. Try one short, safe example and inspect its steps on the watch before relying on it. In the HA mobile app, downloading may require opening the dashboard in an external browser; a downloaded file is not proof of watch delivery.

## Daily schedule and optional Home Assistant publishing

`python cron_daily_update.py` syncs your configured source, processes saved imports, makes at most one automatic coach request, renders saved state and records health. Offline source mode skips external activity sync, while coaching remains a live provider operation. A failed review retains the previous plan and blocks normal HTML publication.

For optional HA publication, create your own private `upload.sh` with one simple SCP command from this checkout's absolute `dashboard.html` path to your destination. Authenticate SSH manually first. Do not commit keys or uploader details. Example only:

```sh
#!/bin/sh
scp -i ~/.ssh/your_key /absolute/path/ai-coach/dashboard.html your-user@your-host:/absolute/remote/www/dashboard.html
```

Make it executable on Linux. Set `COACH_PUBLISH_ENABLED=true`. An existing `upload.sh` is detected for backward compatibility when that setting is absent; explicit `false` disables delivery. Supported status transport is a single SCP command with optional `-i` and `-P`, a `user@host` destination and an absolute remote path. Arbitrary shell pipelines are not parsed.

Adapt these cron entries to your actual checkout and virtual environment; times are the server's timezone. The example assumes UTC:

```cron
0 3 * * * cd /absolute/path/ai-coach && /absolute/path/ai-coach/.venv/bin/python cron_daily_update.py >> cron_output.log 2>&1
15 4 * * * cd /absolute/path/ai-coach && /absolute/path/ai-coach/.venv/bin/python cron_daily_update.py --retry-pending >> cron_output.log 2>&1
0 5 * * * /absolute/path/ai-coach/.venv/bin/python publish_if_ready.py --project /absolute/path/ai-coach --upload-script /absolute/path/ai-coach/upload.sh >> upload.log 2>&1
```

The 04:15 retry does nothing after success. It only retries an isolated pending HTTP 500/502/503/504/529 server failure from that UTC day after at least an hour and within the remaining automatic allowance. Quota/authentication/validation errors do not qualify. Two immediate requests to the same overloaded model are avoided. The 05:00 gate requires a fresh successful complete pipeline; it never treats a cached old success as a new run.

A separate `coach-status.json` reports failures through the same SSH destination even when HTML publication is blocked. The page refreshes this status every minute and when reopened. Missing status is unknown, interrupted work is incomplete and status older than 30 hours is stale. If SSH itself fails, the previous status eventually becomes stale. This is status visibility, not a push notification. Local installations without an uploader do not fail because HA is absent.

## When Garmin needs attention

The terminal and dashboard report **Input required: Garmin** if the Personal API key is missing or Garmin rejects it, or if data permission/account binding needs checking. The prompt tells you how to update the private `.env` and shows the last confirmed activity sync. The failure sidecar still reaches the previous HA page when a failed sync blocks a new HTML upload. The browser never asks you to paste a key into a form.

A rejected key may be expired, revoked, invalid or lack permission; the software does not assume the cause. A one-year key duration does not establish its exact expiry date, so no expiry countdown is invented. Short-lived access tokens are separate: routine token expiry is renewed automatically, and a single rejected cached access token gets one bounded renewal/retry. Repeated credential rejection stops automatic authentication storms until the key changes or an explicit token refresh is requested. A token-only check does not claim that the activity sync succeeded; the action clears only after a verified full sync.

If the same key/access settings are corrected after a rejection, explicitly run `python garmin_sync.py --refresh-token`, then `python garmin_sync.py`, to clear the saved credential rejection. For a new key, replace it privately in `GARMIN_PERSONAL_API_KEY`. Use `python garmin_sync.py --accept-key-rotation` only when you have confirmed the replacement belongs to the same Garmin account. Use a fresh workspace for another account/region or lost binding. If requests are temporarily rate-limited, wait for the saved retry time rather than changing credentials.

## Data, privacy and limitations

Your profile, activity history, descriptions/private notes, check-ins and recent schedule are sent to the selected model provider as coaching context. Review your provider/endpoint policies before connecting sensitive data. The generated HTML contains athlete notes, health/injury context and AI feedback; treat it as private. Home Assistant's `/local/` static files may be accessible without logging in: do not rely on the mobile app's login to protect them. Use an access-controlled hosting route when needed. Never publish your actual HTML, `.env`, token caches, database, profiles, plans, response snapshots or backup archives to GitHub.

Configured zones are athlete-supplied estimates, not medical validation. Missing HR/elevation/effort data limit interpretation. Walking can be a valid terrain or pacing choice; the model should explain it using current evidence. A model-generated plan and trajectory assessment can be wrong. There is no readiness score, automatic activity-to-session completion matching or authenticated feedback form in the static dashboard.

## Updates, recovery and validation

Back up the whole checkout plus a consistent SQLite backup and current cron before updating. Keep profile, canonical JSON, derived Markdown and database together. Install updated source/dependencies, run offline tests and `--dry-run`, then render before restoring schedules. Never replace your `.env` or profile with examples.

```sh
python run_offline_tests.py
python coaching_eval.py
python coaching_eval.py --db ai_coach_master.sqlite --request-id REQUEST_ID --output eval-report.json
```

Evaluation is offline unless explicitly invoked with its documented `--live` option. It presents structural checks and a qualitative rubric, not a guarantee of coaching quality. Actual API requests/usage are stored separately from cached reviews. Saved-response replay is an operator recovery tool for a corrected validator: `python ai_coach.py --replay-request-id REQUEST_ID --dry-run` previews it without calls, and replay without `--dry-run` requires identical current inputs and records an audit. Never reset request counters or alter the question to evade budgets. The older `--operator-extra-attempt` recovery path remains restricted to the same pending review after two 503 failures.

For a deliberate manual exception after the normal daily allowance is exhausted, run `python ai_coach.py --manual-extra-attempt YYYY-MM-DD --manual-reason "I confirm one extra coaching request today"`, using today's UTC date. This permits one additional provider request per UTC day and records it as `manual_override`; failed or invalid replies still use that exception. The manual and older operator-recovery paths share this single daily exception. Add `--new-plan` only when you intend to replace the future plan. The extra request has no automatic retry or fallback, keeps the normal limit unchanged, and cannot be requested by a scheduled run. A stale date is rejected. Provider quotas, account access and any charges still apply; the app cannot override them.

| Symptom | Action |
| --- | --- |
| Incomplete profile/zone error | Enter your own values or explicitly retain `**Heart Rate Zones:** Not configured`; run `setup_coach.py --doctor` |
| Missing dependency | Install `requirements.txt`; official Garmin uses the same HTTP client |
| Authentication/model access error | Check selected provider, key and model access; do not paste keys into issues |
| Garmin account binding/key changed | Preserve the old workspace; use `--accept-key-rotation` only for a confirmed same-account replacement key, otherwise create a fresh workspace |
| Pending review after 503 | Retain last-good state; deferred eligible retry uses the remaining allowance |
| Output rejected/truncated | Inspect private saved request/error; consider a larger configured output limit for a later authorized attempt |
| Pending commit journal | Let normal live recovery resolve it; do not delete state or publish around the guard |
| Unknown dashboard health | Serve page and sibling status from the same origin; inspect local logs without sharing private payloads |

Licensed under the [MIT License](LICENSE), copyright 2026 AI Coach contributors. Bundled assets retain their own [third-party notices](docs/THIRD_PARTY.md). Local ZIP builds do not create GitHub repositories or publish GitHub Releases.

The [release review](docs/RELEASE_REVIEW.md) records corrected findings, verified scope and remaining priorities.

## Acknowledgements

AI-assisted development and review: **OpenAI Codex**.

Codex assisted with implementation, tests, documentation and review under the project owner's direction. Maintenance, approval of changes and release decisions remain the project owner's responsibility. This acknowledgement credits AI assistance; it does not imply OpenAI maintains or endorses the project.
