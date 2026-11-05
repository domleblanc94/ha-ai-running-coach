# AI Running Coach

AI Running Coach is an experimental community app for one athlete. It uses Home Assistant ingress to open the setup and dashboard. Start it from the app page and select **Open Web UI**.

## Add to the Home Assistant sidebar

In Home Assistant, go to **Settings → Apps → AI Running Coach → Info** and turn on **Show in sidebar**. The menu entry is named **AI Running Coach** and uses the running icon. It is available to Home Assistant administrators. You can turn it off on the same page.

If you installed from the documented repository URL, you can jump to the app page. [Open AI Running Coach in Home Assistant](https://my.home-assistant.io/redirect/supervisor_app/?app=ce7ba695_ai_running_coach)

## First setup

1. Create your athlete profile and set your availability. Use A for your main goal and B or C for supporting goals. Each goal can have an optional date and target. A race can also have ideal, main and fallback outcomes for the same event. With no target or outcome, the race goal defaults to **Just finish**. Existing target values carry over when you upgrade. Choose your heart-rate zone mode in the same form.
2. Choose your activity source. Offline mode skips Garmin sync and starts with the profile as its evidence. For Garmin mode, obtain your own official Personal API key and review the permitted use of your data. Configure the key and account region in the app. [Garmin developer program](https://developer.garmin.com/gc-developer-program/overview/)
3. Select a model provider and enter its key. Generating or reviewing a plan uses that provider, including with the offline activity source. The default budget counts up to two automatic requests per UTC day. Check your provider's account limits and pricing separately.
4. Save your IANA timezone (for example, Europe/London) with scheduling unchecked. This controls plan dates and the daily review time. Request counts follow UTC days.
5. Generate and review the first plan. Enable a local review time when you are ready for scheduled reviews. The app keeps the last saved dashboard if a review fails. The scheduler runs one review per local day. If it fails, use **Run now** after checking the cause. Manual runs still use the core request budget. A run interrupted by a restart pauses the schedule until you re-enable it.

Local summary import is currently available through the standalone project. Use the Home Assistant app logs to diagnose startup failures. Keep credentials, athlete data and model response snapshots out of support reports.

## Learn HR zones from baseline runs

Choose **Learn HR zones from baseline runs** in the athlete profile to ask the coach to include conversational baseline runs in the normal training plan. After Garmin sync, use **Baseline run observations** to select one of up to 12 eligible runs from the past 21 days. Add your perceived effort, talk-test result and sensor type. The coach can review those observations during the next normal review under the existing request budget.

Learn mode preserves any saved ranges. If you have no saved ranges, they stay unknown. If you have ranges you want to use, switch to **Enter configured ranges** and save them yourself. Baseline check-ins are stored privately and can form part of the coaching context sent to your selected provider.

## Troubleshoot a failed run

Start with **Coach Status** in the app. A failed run shows its time, stage, reason code and recorded model request count when available. Home Assistant also shows a short diagnostic under **Settings → Apps → AI Running Coach → Log**. After upgrading from an earlier beta, check Coach Status before retrying. The app can recover a more specific explanation from records already saved for that run. If those records are incomplete, the reason can still be unknown.

- **Garmin or activity sync:** check the selected activity source and Garmin access. If Garmin is limiting requests, wait before trying again.
- **Provider authentication or request:** check the selected provider and model in Settings, and confirm the key and model access with that provider.
- **Provider quota or daily budget:** check the provider account or wait for the next UTC day if the app's daily allowance is spent.
- **Truncated or invalid response:** the app keeps the last valid plan. Check the reason and request allowance before retrying.
- **Rendering, publication or storage:** check the app logs and storage. Back up the app before reinstalling or restoring it.

For support, share the app version, failure time, stage, reason code and recorded model request count. If the code points to the provider, include its name and the model identifier. Keep keys, raw provider responses, prompts, profiles and activity records private.

If the app has no saved plan yet, select **Generate new plan**, or **Plan baseline runs** in learn mode. **Run now** is available after the first plan is saved.

## Data and backups

The app stores its data under `/data/app` in Supervisor's private app volume. Home Assistant controls access through ingress. The profile, activity summaries, notes and other coaching context can be sent to the configured model provider during reviews. Check its data handling terms before enabling live reviews.

Include this app in a Home Assistant backup before updating. The Supervisor stops the app while taking its backup, allowing SQLite state to be saved consistently. Protect backups as you would the original data and keys. Restore the app with its data and verify the account binding, request history and dashboard before resuming a schedule.

## Moving from the standalone installation

This beta has no guided migration from a standalone installation. The existing installation and this app have independent databases, Garmin binding and request ledgers. Treat the app as a separate trial until you have a verified migration.

A future cutover needs a consistent source backup and one active scheduler. The migration needs to preserve the athlete profile and plans, SQLite request history for daily limits, and the exact Garmin account binding and source provenance. Copying only the athlete profile, or starting a new database against the same account on the same day, loses that continuity. Verify the restored files and review state with the schedule disabled. Keep the original installation and backup available for rollback. Stop the app schedule before resuming the standalone scheduler.

## Garmin key replacement

Garmin account binding is preserved if a key changes. This beta has no in-app confirmation for key rotation. Back up the app and pause its schedule before rotating a key. Ask the maintainer for the same-account recovery procedure, and keep the sync checkpoint with its matching database. An account or region change requires a new workspace.

FIT workout export remains experimental and requires manual transfer to a compatible watch.
