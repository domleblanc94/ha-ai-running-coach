# Changelog

## 0.1.0-beta.9

- Clarify replacement-plan instructions with fresh session identifiers and explicit weekday availability.
- Handle redundant deletion of known future sessions during a valid replacement, while retaining historical, identifier and training-day checks.
- Show specific reasons for unsupported replacement instructions and reused session identifiers, including matching saved failures from before the update.

## 0.1.0-beta.8

- Align partial weeks with the correct weekday columns in the training calendar.
- Correct the display of saved dashboards without generating a new plan or spending an AI request.
- Add optional preferred long-run days in the athlete setup, treated as alternative days for the coach to choose from.
- Reject new plans with multiple running sessions on one date and identify conflicts in an existing plan. A run and a strength session can still share a date.

## 0.1.0-beta.7

- Make the normal daily coaching allowance editable in Setup & settings, from 1 to 20 requests, with a default of 2.
- Add a confirmed **Run once anyway** action for one extra request per UTC day after the normal allowance is spent.
- Show request usage and the UTC reset time without making a provider request. Scheduled reviews keep their normal allowance.

## 0.1.0-beta.6

- Add an Athlete profile page and open the training dashboard after initial setup.
- Remove the training dashboard's Table view.
- Download the training calendar as PDF and export the saved athlete and training plan Markdown files.
- Apply explicit training-day availability when validating new coaching plans.

## 0.1.0-beta.5

- Show the failed stage, reason and time in Coach Status and write a short, redacted diagnostic to the Home Assistant app log.
- Check saved records for a more specific reason when an earlier beta recorded only a generic failure.
- Explain how to create the first plan or baseline plan before **Run now** becomes available.

## 0.1.0-beta.4

- Add editable A, B and C goals and optional outcomes within each race. Untargeted races default to **Just finish**, and existing targets carry over.
- Add a baseline learning mode and activity-linked heart-rate check-ins.
- Show how to add the app to the Home Assistant sidebar.

## 0.1.0-beta.3

- Install from prebuilt amd64 and arm64 images in GitHub Container Registry.
- Fetch Debian packages over HTTPS when building the image from source.
- Pin the upstream coaching engine with a compact first-plan request to reduce truncated replies during initial setup.

## 0.1.0-beta.1

- Initial Home Assistant app beta, packaged separately from the standalone project.
- Ship a pinned copy of the standalone coaching engine and dashboard assets.
- Serve the setup and coaching dashboard through Home Assistant ingress.
- Persist athlete data and coaching state in the app's private data directory.
