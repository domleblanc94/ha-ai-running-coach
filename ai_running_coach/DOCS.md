# AI Running Coach

AI Running Coach is an experimental community app for one athlete. It uses Home Assistant ingress to open the setup and dashboard. Start it from the app page and select **Open Web UI**.

## Add to the Home Assistant sidebar

In Home Assistant, go to **Settings → Apps → AI Running Coach → Info** and turn on **Show in sidebar**. The menu entry is named **AI Running Coach** and uses the running icon. It is available to Home Assistant administrators. You can turn it off on the same page.

If you installed from the documented repository URL, you can jump to the app page. [Open AI Running Coach in Home Assistant](https://my.home-assistant.io/redirect/supervisor_app/?app=ce7ba695_ai_running_coach)

## First setup

1. Create your athlete profile and set your availability. Use A for your main goal and B or C for supporting goals. Each goal can have an optional date and target. A race can also have ideal, main and fallback outcomes for the same event. With no target or outcome, the race goal defaults to **Just finish**. Existing target values carry over when you upgrade. Choose your heart-rate zone mode in the same form.
2. Choose your activity source. Offline mode skips Garmin sync and starts with the profile as its evidence. For Garmin mode, obtain your own official Personal API key and review the permitted use of your data. Configure the key and account region in the app. [Garmin developer program](https://developer.garmin.com/gc-developer-program/overview/)
3. Select a model provider and enter its key. Generating or reviewing a plan uses that provider, including with the offline activity source. The default allowance is two coaching requests per UTC day, shared by manual and scheduled runs. Check your provider's account limits and pricing separately.
4. Save your IANA timezone (for example, Europe/London) with scheduling unchecked. This controls plan dates and the daily review time. Request counts follow UTC days.
5. Generate and review the first plan. Enable a local review time when you are ready for scheduled reviews. The app keeps the last saved dashboard if a review fails. The scheduler runs one review per local day. If it fails, use **Run now** after checking the cause. Manual runs still use the core request budget. A run interrupted by a restart pauses the schedule until you re-enable it.

Local summary import is currently available through the standalone project. Use the Home Assistant app logs to diagnose startup failures. Keep credentials, athlete data and model response snapshots out of support reports.

## Coaching request allowance

In **Setup & settings**, set **Daily AI request limit** from **1 to 20** requests. The default is **2**. Saving a different allowance does not start a coaching run or clear the requests already recorded today. Each app run still makes at most one coaching request; raising the daily limit does not add automatic retries. Coach Status shows **AI requests today** and the next reset at **00:00 UTC**, which may differ from midnight in your selected timezone. Failed provider attempts count towards the allowance; offline previews and reading the dashboard do not.

When the normal allowance is spent, **Run once anyway** lets you explicitly confirm one extra coaching request for that UTC day. Choose **Review current plan** or **Generate new plan**; with no saved plan, the action generates the first plan. The extra request leaves the normal allowance unchanged. It is spent when the AI request is reserved in the saved ledger, including an attempt that is interrupted or fails. A sync or preflight failure before that reservation, or reuse of a cached review without a new request, leaves the extra available for another explicit confirmation. There is no automatic retry. Restarting the app does not restore a spent extra, and a confirmation for a previous UTC day cannot spend a request on the new day. Scheduled runs cannot use this exception.

The allowance is an app limit, not a monetary spending cap. Increasing it or confirming an extra request does not override Gemini/Google or any other provider's quota, rate limits, account permissions or pricing. If the provider itself reports a quota error, wait for its reset or review the provider account before requesting more work.

## Profile, training days and downloads

After initial setup, the app opens on **Training dashboard**. Use **Athlete profile** to review your saved goals, available training days, running background and heart-rate settings. Use **Setup & settings** when you want to edit them.

The calendar keeps dates in their Monday-to-Sunday columns, including a first week that starts partway through the week. **No session planned** means the saved plan has no entry for that date; it is distinct from a prescribed rest day. Multiple sessions on one date appear together. Compact mobile layouts omit empty gaps. Updating the app corrects the display of an existing dashboard without requiring a new coaching request or changing its saved session dates.

Save changes to available training days before generating a replacement plan. With no days selected, availability is unspecified and the coach chooses the days. Once days are selected, a new plan must keep future training within them. Rest days may fall on any day, and an actual configured race can remain on its booked date. A saved profile change does not rewrite the existing plan by itself: the coach must return a valid new plan before it replaces the upcoming schedule. Completed and historical sessions remain part of your training record.

Use **Preferred long-run days (optional)** to offer the coach one or more suitable days. Select several to offer alternatives; this does not ask for a long run on every selected day. Use **No preference** to clear the selection and let the coach choose from your available days. If you have selected available training days, preferred long-run days must be among them. Save the profile before requesting the next review or replacement plan.

The app currently supports one planned running session per date. A run and a strength session may share a date. If the coach moves a run to a date that already has a run, it must reconcile the existing session in the same update. A conflicting response is rejected and the last valid saved plan is retained. Conflicts already present in an older plan are shown for review; saving settings or updating the app does not silently move or delete those sessions.

Download the calendar as a PDF for printing or sharing. You can also download **athlete.md** and **training_plan.md**. These exports contain your private profile and training information; store and share them accordingly. They do not contain connection API keys. Exporting saved data does not make an AI request.

## Learn HR zones from baseline runs

Choose **Learn HR zones from baseline runs** in the athlete profile to ask the coach to include conversational baseline runs in the normal training plan. After Garmin sync, use **Baseline run observations** to select one of up to 12 eligible runs from the past 21 days. Add your perceived effort, talk-test result and sensor type. The coach can review those observations during the next normal review under the existing request budget.

Learn mode preserves any saved ranges. If you have no saved ranges, they stay unknown. If you have ranges you want to use, switch to **Enter configured ranges** and save them yourself. Baseline check-ins are stored privately and can form part of the coaching context sent to your selected provider.

## Troubleshoot a failed run

Start with **Coach Status** in the app. A failed run shows its time, stage, reason code and recorded model request count when available. Home Assistant also shows a short diagnostic under **Settings → Apps → AI Running Coach → Log**. After upgrading from an earlier beta, check Coach Status before retrying. The app can recover a more specific explanation from records already saved for that run. If those records are incomplete, the reason can still be unknown.

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

This beta has no guided migration from a standalone installation. The existing installation and this app have independent databases, Garmin binding and request ledgers. Treat the app as a separate trial until you have a verified migration.

A future cutover needs a consistent source backup and one active scheduler. The migration needs to preserve the athlete profile and plans, SQLite request history for daily limits, and the exact Garmin account binding and source provenance. Copying only the athlete profile, or starting a new database against the same account on the same day, loses that continuity. Verify the restored files and review state with the schedule disabled. Keep the original installation and backup available for rollback. Stop the app schedule before resuming the standalone scheduler.

## Garmin key replacement

Garmin account binding is preserved if a key changes. This beta has no in-app confirmation for key rotation. Back up the app and pause its schedule before rotating a key. Ask the maintainer for the same-account recovery procedure, and keep the sync checkpoint with its matching database. An account or region change requires a new workspace.

FIT workout export remains experimental and requires manual transfer to a compatible watch.
