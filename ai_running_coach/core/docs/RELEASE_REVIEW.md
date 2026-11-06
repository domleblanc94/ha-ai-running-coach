# Public source release review

Status: **public beta 0.2.3b6**, intended for technical self-hosters running one personal source-checkout installation. It is not a stable, universally validated provider/device integration. Repository pushes and GitHub Release publication are separate maintainer operations. A separate personal Linux installation was backed up, migrated and checked against the official Garmin Personal API. Provider validation scope is recorded below.

## Findings corrected

- Added an offline browser FIT-workout preview/editor/download path with explicit step durations/targets, lap-button options, and no cloud-send claim. Legacy ambiguous sessions require review rather than guessed recovery/pace or added distance.
- Replaced the previous activity connectors with the official Garmin Personal API and retained offline/manual imports; authentication and source provenance are explicit.
- Added actionable Garmin key/auth prompts in terminal and HA status, including compatibility with a retained older dashboard. The public sidecar uses fixed safe text and never publishes keys, tokens or account fingerprints. A one-year key duration is not treated as a known expiry date.
- Added isolated, resumable official historical imports with actual earliest-date discovery, bounded requests and independent top-level activity-ID verification/repair. Historical work does not mark old windows as a fresh daily sync.
- Added a first-run wizard, explicit unknown HR zones, empty database initialization and a doctor command. Setup preserves existing private files and never assigns demonstration physiology to a real athlete.
- Added a separate conspicuously synthetic offline demo and local CSV/JSON import schema. Missing values remain unknown; stable dataset IDs and backups support repeated imports.
- Added explicit Gemini/OpenAI/Anthropic adapters and custom OpenAI-compatible endpoints without automatic cross-provider fallback. Account/model selection, endpoint identity, actual request budgets and sanitized errors are retained.
- Updated new installations to the documented stable `gemini-3.5-flash-lite` default with compatible request parameters. Existing explicit model choices are preserved; the offline doctor reports the deprecated Gemini 2.5 Flash access/routing notice rather than silently migrating users.
- Added transactional replacement of future training blocks after valid AI output; past sessions and last-good recovery state are preserved.
- Made activity sync and HA publishing optional for local use. Kept delayed transient-server retries within the automatic allowance and normal publication gates.
- Hardened public packaging with an explicit source/asset/example allowlist and symlink rejection. Credentials, personal profiles, runtime plans, SQLite files, rendered real dashboards, caches and archives are excluded.
- Stored official Garmin tokens/checkpoints privately, bound imports to an explicit account/key identity, and prevented old histories being relabelled. Retained bundled upstream licenses and MIT licensing for project source.
- Added socket-blocked offline tests, configuration isolation, cold-install checks and a cross-platform CI workflow.

## Verified scope

Local verification uses Windows and CPython 3.13. The clean-source flow installs only base HTTP/dotenv dependencies in a new virtual environment, initializes without an existing profile/database/plan, runs an offline new-plan preview, imports synthetic summaries, and renders a synthetic dashboard. The latest network-blocked suite ran **208 tests: 204 passed and four optional checks skipped**. Seven FIT checks separately passed with Garmin's official decoder available. Provider protocol and failure-path tests use mocked HTTP responses; these establish request handling and validation behavior, not coaching quality.

During beta 1, one separately authorized live evaluation request to `gemini-3.5-flash-lite` succeeded with a 4096-token output cap. Provider-reported usage was 2861 input and 875 output tokens. The synthetic response contained three future calendar entries: a 30-minute easy running workout with an open/conversational target and two rest days, plus an `insufficient_evidence` trajectory. The same saved response then passed normal validation, journaled SQLite/file plan application and HTML rendering offline, without another API call. Production configuration, profile, plan and database were unchanged. This checks one account/model and a small synthetic end-to-end plan path; it does not establish free-tier eligibility, actual billed cost or coaching suitability for a real athlete.

Physical Forerunner 965 transfer/execution remains untested, so FIT export is experimental and does not claim automatic watch delivery. A personal-installation check additionally verified official Garmin authentication, current and historical sync, and Linux deployment. This is one installation, not universal account/device compatibility. OpenAI/Anthropic/custom endpoint live access, real-athlete coaching quality and provider billing remain unverified.

The GitHub Actions workflow targets Linux/Windows and Python 3.11-3.13. Check the public repository's run for the exact beta commit; local results do not establish that this beta has passed the public CI matrix. This is a source-checkout application: installed module wheels alone do not include adjacent dashboard/template assets. Run the supplied scripts from the extracted tree.

### Beta 2

First-plan generation and explicit plan replacement now ask for a compact seven-day block with at most seven entries and one optional structured workout. A truncated response still leaves the previous dashboard in place and reports the output limit in the status panel. The ordinary review flow and request budget are unchanged.

### Beta 3

Explicit weekly training days are validated before new coaching changes are committed. The coach still chooses the sessions; it must produce a plan that fits the athlete's saved availability. Existing historical sessions remain unchanged. The training dashboard no longer offers Table view.

### Beta 4

The normal daily coaching allowance remains two requests and can be configured from 1 to 20. A separate, explicitly confirmed manual exception permits one extra request per UTC day after that allowance is exhausted. The exception is dated, recorded in the request ledger and unavailable to scheduled runs. Failed provider attempts still count; changing an app allowance does not change a provider's quota, account access or pricing.

### Beta 5

Calendar sessions align with their actual weekdays when the first displayed week starts partway through the week. Days without an entry show **No session planned** in the Monday-to-Sunday grid, distinct from an explicit rest day, and sessions on the same date stay together. The compact mobile layout avoids empty gaps. This is a display correction; it does not change saved session dates or request a new coaching plan.

The athlete profile can specify preferred long-run days as alternatives for the coach to choose from. Future plan changes must avoid putting multiple running sessions on the same date; a run and a strength session can still share a date. Moving a run onto an occupied running day therefore requires the coach to reconcile the existing session in the same valid update. Existing plan conflicts remain visible until a valid coaching update resolves them; the software does not silently delete or move sessions.

### Beta 6

Replacement-plan requests supply clearer instructions for fresh session identifiers and explicit date/weekday availability. The replacement flow can accept redundant deletion of known future sessions while preserving checks against historical changes, reused or retired identifiers, unavailable training days and duplicate runs. Rejected responses retain the saved plan; this update does not replay an earlier response or request a new one automatically.

## Beta tester path

Start with the synthetic no-call demo, then create your own offline workspace and edit the explicitly unknown profile fields. Run `setup_coach.py --doctor` and `ai_coach.py --dry-run --new-plan` before the first live request. Use your own permitted activity data and keys, inspect the automatically saved plan, and enable cron/HA publishing only after the manual flow works. Keep backups before updates. For FIT testing, manually transfer one small example and inspect the native watch steps; generating a valid file is not proof of transfer or execution. Reports should include version, OS/Python, selected provider/model and sanitized error text, without personal profiles, keys or request snapshots.

## Remaining priorities

1. Validate official Garmin Personal API access and permitted downstream AI use against your own account and applicable terms. The adapter has mocked protocol coverage and one personal-installation sync smoke check; other accounts and service variations remain unverified. It is personal access, not a multi-user commercial integration. Legacy histories must not be relabelled as new official Garmin observations. Local files must be independently entitled data.
2. Validate selected provider models and custom endpoints against actual authorized accounts, with explicit call/cost limits. Compatibility tests cannot establish account access, pricing or coaching quality.
3. Consider native FIT/GPX or a documented exporter conversion flow after source permission is settled. Current local import accepts explicitly mapped CSV/JSON summaries only.
4. Deploy athlete dashboards behind appropriate access control. The generated HTML contains personal notes/health context; HA `/local/` static paths may not require login. Serve only intended output files, never the project/data directory.
5. Replace the retained Tailwind browser runtime with compiled CSS and test supported mobile browsers when a public hosting/build workflow is chosen. Preserve the existing UI and vendored notices.

The AI still owns training decisions. Structural validation, offline checks and positive trajectory text do not certify medical suitability or race readiness.
