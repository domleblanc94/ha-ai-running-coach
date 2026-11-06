# Run AI Multisport Coach on your own computer

This route uses a terminal and Python on Windows, macOS or Linux. If you already use **Home Assistant OS**, the [Home Assistant app](https://github.com/domleblanc94/ha-ai-running-coach#readme) offers setup forms, scheduling and a dashboard in one place. It does not need this separate installation.

Want to see the interface first? [Open the fictional example](https://domleblanc94.github.io/ai-running-coach/). It contains authored sample data, makes no AI/Garmin requests and does not ask for credentials.

## 1. Download and prepare

Install [Python](https://www.python.org/downloads/) **3.11 or newer**. Automated checks cover 3.11–3.13; Python 3.13 is a straightforward choice. Windows users can select the Python launcher during installation. Download [the version 1.0 source ZIP](https://github.com/domleblanc94/ai-running-coach/releases/download/v1.0.0/ai-multisport-coach-1.0.0.zip), extract it to a private folder and open a terminal in the extracted folder containing `setup_coach.py`. Keep its scripts, examples and dashboard assets together.

Create a private Python environment. On **Windows PowerShell**:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On **macOS or Linux**:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

If Linux reports that `venv` is missing, install your distribution's Python venv package and repeat the command. Use the environment's Python for every command below: replace `python` with `.\.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on macOS/Linux. No environment activation or PowerShell policy change is required.

## 2. Run the setup wizard

```sh
python setup_coach.py
```

Choose an AI provider and activity source when prompted. The wizard creates an empty database, `.env` configuration and `athlete.md` profile; it preserves existing files and makes no API calls.

- **Gemini** is the default provider. You need a model available to your account and a provider API key. Free-tier eligibility and quotas depend on that account; the application does not guarantee free usage.
- **Offline** is the easiest activity source to start with: enter your profile and optionally import your own permitted activity summaries. No activity-service key is needed.
- **Garmin** uses the official Garmin Connect Personal API. You need access and a personal key from your own account. The project does not ask for your Garmin password.

Open `.env` in a text editor. Set the key for your chosen provider: `GEMINI_API_KEY`, `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Set `COACH_MODELS` to a model available to you. If you selected Garmin, set `GARMIN_PERSONAL_API_KEY` and the correct `GARMIN_PERSONAL_REGION`. Keep `COACH_PUBLISH_ENABLED=false` for this local setup. Do not put keys in screenshots, issues, Git or the public example.

The [configuration reference](../README.md#select-sports-and-goals) explains model options, request limits and sport fields. The normal default is two coaching requests per UTC day; this is a request limit, not a monetary spending cap.

## 3. Tell the coach about you

Edit `athlete.md`, replacing its placeholder text. Include your name, training experience, current injuries or constraints, available weekdays and your goal. Keep heart-rate zones explicitly unknown unless you have your own configured values. The AI can plan by effort without them.

Set `**Training Mode:**` to `running`, `cycling`, `swimming` or `triathlon`. You can give A, B and C goals with different priorities. For an event, use this format with your own details:

```markdown
**A-Race:** YOUR EVENT
**Sport:** running
**Date:** YYYY-MM-DD
**Target Time:** Just finish
```

Use a real date in `YYYY-MM-DD` format before saving. Goal sport can also be `cycling`, `swimming` or `multisport` for triathlon. Finishing is enough; time targets are optional. Availability uses full weekday names, for example `**Weekly Availability:** Monday, Wednesday, Saturday`. Preferred long-run or long-ride days are optional alternative slots. Pool FIT workouts require an explicit pool length, such as your actual `**Pool Length:** 25 m`.

## 4. Check setup and bring in activities

```sh
python setup_coach.py --doctor
```

Fix any `ACTION:` messages. This checks local files and key presence without contacting providers or displaying keys. `--doctor --offline` can check a keyless local-only installation; it does not verify Garmin access when Garmin is selected.

For **Garmin**:

```sh
python garmin_sync.py
python database_manager.py
```

These commands contact Garmin and import the returned activities; they do not request an AI review. For **offline summaries**, use the [documented CSV/JSON import format](../README.md#local-activity-files) instead. You can also begin with your profile alone; the coach must then work with limited evidence.

## 5. Generate and inspect your first plan

Preview the inputs locally first:

```sh
python ai_coach.py --dry-run --new-plan
```

This prints private profile/activity context to your terminal without calling a model. Check it locally; do not post the output publicly. When ready:

```sh
python ai_coach.py --new-plan "Build a first training block around my profile and availability"
python generate_dashboard.py
```

The first command makes a provider request and may incur costs under your provider plan. A valid response saves `training_plan.json` and `training_plan.md`. If a request fails, the last valid plan is retained; read the terminal error before retrying. The second command builds `dashboard.html` without another AI call. Open that HTML file in your browser and review the plan before training. A live workflow status is unavailable when opening a local file; the saved plan remains usable.

For later reviews, run `python cron_daily_update.py`. It synchronizes the selected activity source, processes data, requests a bounded review and renders the dashboard. To deliberately replace only the future training block, use `--new-plan` again; past sessions are retained.

## 6. Optional private web viewing

The simplest view is opening `dashboard.html` directly. To serve it locally, copy only that file into a separate empty output folder; optionally copy `coach-status.json` if your pipeline created it. Refresh these copies after later runs.

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Path .\web-output -Force
Copy-Item -LiteralPath .\dashboard.html -Destination .\web-output\dashboard.html
.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1 --directory .\web-output
```

macOS/Linux:

```sh
mkdir -p web-output
cp dashboard.html web-output/dashboard.html
.venv/bin/python -m http.server 8000 --bind 127.0.0.1 --directory web-output
```

Open `http://127.0.0.1:8000/dashboard.html` on that computer. This binds only to that computer and provides no remote phone access. Stop it with Ctrl+C. For private remote viewing, use your own authenticated hosting or the Home Assistant app; do not expose this development server to the internet.

**Never serve or publish the project folder.** It contains your keys, profile, activities and model-request history. GitHub Pages hosts only the repository's fictional example, never your personal dashboard. Anyone who can read a personal dashboard can read the training information embedded in it.

## 7. Optional scheduling, after a successful manual run

Scheduling is separate from installation. Keep one scheduler for this workspace to avoid competing jobs. On Linux, use `crontab -e` and adapt this example to your actual absolute paths:

```cron
0 6 * * * cd /absolute/path/to/coach && /absolute/path/to/coach/.venv/bin/python cron_daily_update.py >> /absolute/path/to/coach/cron.log 2>&1
```

On Windows, Task Scheduler can run `.venv\Scripts\python.exe` with argument `cron_daily_update.py` and **Start in** set to the extracted project folder. The computer must be awake. macOS users can use their normal local scheduler. Automatic runs keep the configured request limits; provider quotas still apply. Updating a separate private web-output copy requires a separate copy/publication step.

## Backups, updates and limits

Before updating, privately back up your project data, including `.env`, `athlete.md`, `training_plan.*`, `ai_coach_master.sqlite`, hidden runtime files and activity folders. Stop your scheduler during the update, replace public code/assets from the release and rerun the doctor check. Do not replace your data with the synthetic example.

Version 1.0 labels the packaged application, not universal provider or device certification. OpenAI/Anthropic/custom endpoint compatibility remains experimental beyond mocked tests. FIT files have independent decoder checks, but physical watch transfer/execution remains unverified. Transfers are manual; this release does not automatically upload or delete workouts on a watch. See the [release review](RELEASE_REVIEW.md) for the evidence and limitations, and report sanitized problems in the [standalone project](https://github.com/domleblanc94/ai-running-coach/issues).
