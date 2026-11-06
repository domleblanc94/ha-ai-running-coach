# AI Running Coach for Home Assistant

**Experimental community app, version 0.1.0-beta.6.** It brings the standalone AI Running Coach into Home Assistant's app UI. The app packages a pinned copy of the upstream engine and keeps its own athlete profile, activity history, request ledger and plans.

- Install on Home Assistant OS on amd64 or aarch64. Supervisor pulls the prebuilt image for your architecture from GitHub Container Registry.
- Open setup and the running dashboard through Home Assistant ingress. The app stores its data privately and needs no exposed host port.
- Start from your athlete profile, or add activity evidence through the official Garmin Personal API. Review the generated plan before using it for training.
- View your saved athlete profile and export the training calendar as PDF, or download the athlete and training plan Markdown files.
- The standalone installation remains available for people who run the scripts directly. The two installations maintain separate state.

This community project is independently maintained. [Standalone AI Running Coach](https://github.com/domleblanc94/ai-running-coach)

## Install

[Open the Add repository dialog in your Home Assistant](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fdomleblanc94%2Fha-ai-running-coach)

1. In Home Assistant, open **Settings → Apps → Install app**. Older interfaces use **Add-ons** and **Add-on Store**.
2. Open the store menu and select **Repositories**. Add `https://github.com/domleblanc94/ha-ai-running-coach`.
3. Install **AI Running Coach**, start it, then select **Open Web UI**. Installation downloads the versioned image from `ghcr.io`.
4. Use the in-app setup and review the profile and request budget before enabling scheduled reviews. Turn on **Start on boot** on the app page if Home Assistant should restart it after a reboot. Turn on **Show in sidebar** on the same page to add AI Running Coach to the main menu.

[Setup, privacy, backups and standalone migration](ai_running_coach/DOCS.md)

If an earlier installation failed while fetching a Debian package, refresh the app store and check that it offers **0.1.0-beta.6**, then retry installation. The new installation downloads the prebuilt image. Home Assistant needs HTTPS access to GitHub and GitHub Container Registry.

## Development

Run `python scripts/vendor_core.py` to verify the vendored file list and digests. Build the app using `docker build -t ai-running-coach-ha ai_running_coach`. Run `python scripts/smoke_container.py ai-running-coach-ha` to exercise the built container on an isolated synthetic ingress network. The smoke test does not use athlete files or API credentials.

Maintainers publish a candidate image by pushing an `image-v<VERSION>` tag at the intended source commit. The image workflow smoke-tests amd64 and arm64 on their native runners before creating the versioned manifest. Verify anonymous pulls for both architectures before advertising that version in `config.yaml`. Use a new version for a replacement image.

The upstream commit, Git tree and release-manifest digest are in [core-pin.json](ai_running_coach/core-pin.json). To propose an update, fetch the public standalone repository and stage a released commit into a new directory:

```sh
python scripts/vendor_core.py --upstream-checkout /path/to/ai-running-coach --commit FULL_COMMIT_SHA --stage /path/to/new-staging-directory
```

Review the staged pin and upstream changes. Replace the vendored directory and pin as a pair, run the wrapper and container checks, and bump the app version. Submit coaching-engine changes to the standalone project first.

The repository uses the MIT license. Bundled dashboard components retain their upstream notices. [Third-party notices](ai_running_coach/core/docs/THIRD_PARTY.md)
