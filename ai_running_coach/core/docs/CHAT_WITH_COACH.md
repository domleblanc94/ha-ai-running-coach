# Chat with your coach

The optional connector lets your existing chat assistant read saved coaching data, record feedback you explicitly report, and propose a training change. It makes no nested AI/provider requests. Your chat subscription or provider may have its own usage charges. Normal daily coaching and dashboards do not require MCP.

A proposal does not change your plan. Review its exact before/after sessions in the coach app and select Apply or Reject. The connector exposes no approval tool. Profile, plan contents, revision and athlete date must still match when you approve; otherwise create a fresh proposal.

## Choose where the data lives

- **Home Assistant:** keep HA as the live source. Run the small stdio connector on a desktop and relay its six bounded actions to the HA bridge. Do not use an old desktop copy of the plan as the workspace.
- **Standalone:** pass the explicit local data directory to `--workspace`. This directory must contain `athlete.md`, `training_plan.json` and `ai_coach_master.sqlite` with approved activity provenance.

The old VM does not need to run. No command here enables public hosting, port forwarding, a tunnel, or a user account automatically.

## Install on the desktop

Use Python 3.11 or newer. From this source checkout:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[mcp]"
```

The optional extra uses the [official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk). All stdio output belongs to MCP; connector failures print only sanitized messages to stderr.

### Connect to Home Assistant

1. Open the coach app through authenticated HA ingress. Enable the optional Chat bridge and generate its separate connector token. Keep that token private; rotating or disabling it revokes desktop access.
2. Explicitly map the bridge's container port `8100/tcp` to a trusted LAN host port in the add-on Network configuration. The mapping is unset by default. Restart the add-on after changing networking. The ordinary ingress page is not the connector endpoint.
3. In the process that will launch the connector, set `COACH_CHAT_URL` to the bridge origin and `COACH_CHAT_TOKEN` to the token. Do not put the token in a URL, chat message, screenshot, repository, command arguments, or support log.
4. Check with the same launch environment:

```powershell
.\.venv\Scripts\python.exe .\coach_mcp.py --url http://192.168.1.5:8100 --allow-private-http --check
```

Replace the example address with your HA LAN IP and mapped port. Plain HTTP requires explicit `--allow-private-http` and a literal loopback/private LAN IP; it sends the token and reports without transport encryption, so use it only on a trusted LAN. An HTTPS bridge origin is supported without that flag. Redirects and environment proxy inheritance are disabled. Do not expose this port to the public internet.

`--check` reads one day of plan metadata, prints no athlete data and makes no model call. A desktop cannot reach HA unless it is on the same reachable network (or an independently configured private network connection).

### Connect to a standalone workspace

```powershell
.\.venv\Scripts\python.exe .\coach_mcp.py --workspace C:\CoachData --check
```

Use this only for the authoritative standalone workspace. It does not sync a desktop copy with HA.

## Use a desktop MCP client

Configure a client that supports launching a local stdio MCP command. For example, the `mcpServers` entry in Claude Desktop's configuration can launch the Python executable and script using absolute paths:

```json
{
  "mcpServers": {
    "coach": {
      "command": "C:\\coach-source\\.venv\\Scripts\\python.exe",
      "args": ["C:\\coach-source\\coach_mcp.py", "--url", "http://192.168.1.5:8100", "--allow-private-http"]
    }
  }
}
```

Have the client inherit `COACH_CHAT_TOKEN` securely from its launch environment, or use its private environment settings. Restart the client after configuration changes. For standalone use, replace the URL arguments with `--workspace` and the absolute data directory. Do not configure both sources.

## Optional ChatGPT connection

ChatGPT cloud cannot launch this desktop stdio command directly. Where your account supports it, use OpenAI's [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). Follow the current [official tunnel-client onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md), starting with `tunnel-client help quickstart`, and choose its local stdio command binding to the desktop command above. You provision the tunnel and credentials yourself; availability and permissions depend on your account. Keep the desktop, tunnel client and HA network connection running during chat. The tunnel makes an outbound connection; do not publish the HA bridge. Nothing in this project creates a tunnel or account.

## What to say

Start with: “Read my coach context and current training plan. Explain what the saved evidence says and what is unknown.”

For feedback: “Save this as my report for 2026-11-26: fatigue 4/10, no pain, six hours sleep. I don't know my nutrition totals.” The assistant should save only observations you supplied, using a unique request ID and reusing it unchanged on retries. Feedback cannot be dated in the future, and a linked activity must already exist. Grams, millilitres and gel counts are session/period totals, not per-hour rates.

For changes: “Propose a shorter easy session tomorrow because I reported feeling tired. Explain the change and leave it for me to approve.” Review the proposal in HA ingress; chat cannot apply it. Dated reports remain evidence rather than permanent restrictions, and missing values remain unknown.

For a standalone human review from a terminal:

```powershell
.\.venv\Scripts\python.exe .\coach_mcp.py --workspace C:\CoachData --list-proposals
.\.venv\Scripts\python.exe .\coach_mcp.py --workspace C:\CoachData --show-proposal PROPOSAL_ID
.\.venv\Scripts\python.exe .\coach_mcp.py --workspace C:\CoachData --apply-proposal PROPOSAL_ID --proposal-hash EXACT_DISPLAYED_HASH
```

Use `--reject-proposal` with the same displayed hash to reject instead. These terminal review commands are intentionally absent from MCP and from the HA relay.

## Data and limits

The six remote actions are context, plan, recent activities, saved race context, save feedback and propose adjustment. Upcoming plans span at most 366 days; recent activities cover at most 56 days and 50 records. Requests are limited to 128 KiB and results to 512 KiB (520 KiB including the HA envelope).

The connector shares selected profile facts, training sessions, measured activity summaries, athlete reports and saved race strategies with the chat provider you select. It does not provide API keys, tokens, arbitrary file access, raw GPS tracks or route coordinates. Unapproved or unidentified activity provenance blocks all actions. Treat free text in saved records as evidence rather than instructions.

Proposals use existing workout/calendar/sport validators, the shared coach lock and durable plan journal. Saving feedback or proposals and applying approved changes make no AI/provider calls. Applying refreshes the local dashboard; if refresh fails, the response reports that a dashboard refresh is required while retaining the committed plan.

## Troubleshooting

- **Connection rejected:** confirm bridge enabled, current token, explicit network mapping, origin address and desktop network reachability. Rotate a misplaced token through authenticated ingress.
- **Not ready:** finish setup and generate a valid plan in the authoritative workspace.
- **Source policy blocked:** preserve the existing dataset as an archive and initialize an approved official Garmin or local import workspace. Do not relabel old records.
- **Stale proposal:** read the current profile/plan and propose again. A changed date also requires review against current sessions.
- **Busy:** let the existing coaching action finish, then retry with the same request ID.
- **Context too large:** request fewer plan days or activities. Optional SDK failures do not prevent normal core coaching.

No live chat account, tunnel, bridge port mapping or public deployment is required to run the offline test suite.
