# AI Running Coach

AI Running Coach is an experimental community app for one athlete. It uses Home Assistant ingress to open the setup and dashboard. Start it from the app page and select **Open Web UI**. You can add it to the sidebar from the same page.

## First setup

1. Create your athlete profile with your goals and availability. Leave heart-rate zones unknown if you do not know your values.
2. Choose your activity source. Offline mode skips Garmin sync and starts with the profile as its evidence. For Garmin mode, obtain your own official Personal API key and review the permitted use of your data. Configure the key and account region in the app. [Garmin developer program](https://developer.garmin.com/gc-developer-program/overview/)
3. Select a model provider and enter its key. Generating or reviewing a plan uses that provider, including with the offline activity source. The default budget counts up to two automatic requests per UTC day. Check your provider's account limits and pricing separately.
4. Save your IANA timezone (for example, Europe/London) with scheduling unchecked. This controls plan dates and the daily review time. Request counts follow UTC days.
5. Generate and review the first plan. Enable a local review time when you are ready for scheduled reviews. The app keeps the last saved dashboard if a review fails. The scheduler runs one review per local day. If it fails, use **Run now** after checking the cause. Manual runs still use the core request budget. A run interrupted by a restart pauses the schedule until you re-enable it.

Local summary import is currently available through the standalone project. Use the Home Assistant app logs to diagnose startup failures. Keep credentials, athlete data and model response snapshots out of support reports.

## Data and backups

The app stores its data under `/data/app` in Supervisor's private app volume. Home Assistant controls access through ingress. The profile, activity summaries, notes and other coaching context can be sent to the configured model provider during reviews. Check its data handling terms before enabling live reviews.

Include this app in a Home Assistant backup before updating. The Supervisor stops the app while taking its backup, allowing SQLite state to be saved consistently. Protect backups as you would the original data and keys. Restore the app with its data and verify the account binding, request history and dashboard before resuming a schedule.

## Moving from the standalone installation

This beta has no guided migration from a standalone installation. The existing installation and this app have independent databases, Garmin binding and request ledgers. Treat the app as a separate trial until you have a verified migration.

A future cutover needs a consistent source backup and one active scheduler. The migration needs to preserve the athlete profile and plans, SQLite request history for daily limits, and the exact Garmin account binding and source provenance. Copying only the athlete profile, or starting a new database against the same account on the same day, loses that continuity. Verify the restored files and review state with the schedule disabled. Keep the original installation and backup available for rollback. Stop the app schedule before resuming the standalone scheduler.

## Garmin key replacement

Garmin account binding is preserved if a key changes. This beta has no in-app confirmation for key rotation. Back up the app and pause its schedule before rotating a key. Ask the maintainer for the same-account recovery procedure, and keep the sync checkpoint with its matching database. An account or region change requires a new workspace.

FIT workout export remains experimental and requires manual transfer to a compatible watch.
