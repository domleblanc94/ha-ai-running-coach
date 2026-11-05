# Changelog

## 0.1.0-beta.3

- Install from prebuilt amd64 and arm64 images in GitHub Container Registry.
- Fetch Debian packages over HTTPS when building the image from source.
- Pin the upstream coaching engine with a compact first-plan request to reduce truncated replies during initial setup.

## 0.1.0-beta.1

- Initial Home Assistant app beta, packaged separately from the standalone project.
- Ship a pinned copy of the standalone coaching engine and dashboard assets.
- Serve the setup and coaching dashboard through Home Assistant ingress.
- Persist athlete data and coaching state in the app's private data directory.
