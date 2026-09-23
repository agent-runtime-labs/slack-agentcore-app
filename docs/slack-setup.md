# Slack App Setup

Use **two Slack apps**: one for local development (its events go through ngrok to your laptop) and one for AWS (its events go to API Gateway). Slack apps have a single Request URL, so separate apps prevent the two environments from interfering with each other.

## 1. Create the app from a manifest

1. Go to <https://api.slack.com/apps> and choose **Create New App → From a manifest**.
2. Pick your workspace.
3. Paste [slack-app-manifest.yaml](slack-app-manifest.yaml). Replace `request_url` with either:
   - AWS: the `slack_events_url` Terraform output, e.g. `https://abc123.execute-api.us-east-1.amazonaws.com/slack/events`
   - Local: `https://<your-ngrok-host>/slack/events`

   If you don't have the URL yet, delete the `request_url` line and set it later (step 4).
4. Click **Create**.

The manifest configures:

| Setting | Value | Why |
|---|---|---|
| Bot scopes | `app_mentions:read` | Receive `@bot` mentions in channels. |
| | `chat:write` | Post the placeholder, update it, and send ephemeral connect links. |
| | `im:history`, `im:read`, `im:write` | Receive and answer direct messages. |
| | `reactions:write` | React to the user's message (⏳ then ✅/⚠️/🔒) while it's being handled. |
| Bot events | `app_mention`, `message.im` | The only two events the handler processes. |
| App Home | Messages tab enabled, not read-only | Lets users DM the bot. |

<details>
<summary>Prefer clicking through the UI?</summary>

1. **Create New App → From scratch**, then name it and pick a workspace.
2. **OAuth & Permissions → Bot Token Scopes**: add the six scopes above.
3. **App Home**: enable the *Messages Tab*, and tick *Allow users to send Slash commands and messages from the messages tab*.
4. **Event Subscriptions**: turn it on, set the Request URL, and add the bot events `app_mention` and `message.im`.
</details>

## 2. Install and collect the credentials

1. **OAuth & Permissions → Install to Workspace → Allow**.
2. Copy the **Bot User OAuth Token** (`xoxb-…`).
3. Copy the **Signing Secret** from **Basic Information → App Credentials**.

These are secrets. Store them only in `.env` (local) or Secrets Manager (AWS).

## 3. Give the credentials to the app

**Local (Tilt):** in `.env`, set:

```bash
SLACK_DRY_RUN=false
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
```

Then restart Tilt.

**AWS:** after `terraform apply`, run:

```bash
SLACK_BOT_TOKEN=xoxb-... SLACK_SIGNING_SECRET=... \
  ./infra-as-code/scripts/put-slack-secret.sh dev
```

The Lambdas cache the secret per container. After you rotate it, redeploy (or wait for containers to recycle) so the new value is picked up.

## 4. Verify the Request URL

In **Event Subscriptions**, paste the Request URL. Slack sends a signed `url_verification` challenge, and the app answers it after checking the signature. You should see **Verified ✓**.

If verification fails:
- **AWS:** make sure the Slack secret has a value (step 3). Until it does, the Lambda can't check the signature.
- **Local:** make sure the ngrok tunnel is running and `SLACK_SIGNING_SECRET` matches this app.

After changing scopes or events, Slack asks you to **reinstall** the app. Do it, or the new events won't arrive.

## 5. Use it

- **Channel:** `/invite @AgentCore Assistant`, then `@AgentCore Assistant what's my LinkedIn name?` (or `what's my GitHub username?`, see [github-setup.md](github-setup.md))
- **DM:** open the app under *Apps* and message it directly.

Each reply appears in a thread. The first time a person asks about LinkedIn or GitHub, they get a **private** "Connect LinkedIn"/"Connect GitHub" button that nobody else in the channel can see. After connecting, they ask again.

## Behaviour notes

- **Profile data is posted where the question was asked.** Anyone in a public channel who can read the thread will see the answer. For private data, DM the bot.
- **Conversation history is per thread and per person.** Two people in the same thread have separate histories with the agent.
- **Slack retries** (the `X-Slack-Retry-Num` header) are acknowledged but ignored, because the first delivery was already queued.
