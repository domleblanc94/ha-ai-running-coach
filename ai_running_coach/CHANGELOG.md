# Changelog

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
