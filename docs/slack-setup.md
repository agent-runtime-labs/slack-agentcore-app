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
| | `channels:history`, `groups:history` | Read messages in public/private channels the bot is in, so it can reply [without an @mention](#replying-without-an-mention). |
| | `chat:write` | Post the placeholder, update it, and send ephemeral connect links. |
| | `im:history`, `im:read`, `im:write` | Receive and answer direct messages. |
| | `reactions:write` | React to the user's message (👀 then 💬/⚠️/🔒) while it's being handled. |
| Bot events | `app_mention`, `message.im` | @mentions and direct messages: always answered. |
| | `message.channels`, `message.groups` | Every other channel message: answered only if it's meant for the bot. |
| App Home | Messages tab enabled, not read-only | Lets users DM the bot. |

<details>
<summary>Prefer clicking through the UI?</summary>

1. **Create New App → From scratch**, then name it and pick a workspace.
2. **OAuth & Permissions → Bot Token Scopes**: add the eight scopes above.
3. **App Home**: enable the *Messages Tab*, and tick *Allow users to send Slash commands and messages from the messages tab*.
4. **Event Subscriptions**: turn it on, set the Request URL, and add the bot events `app_mention`, `message.channels`, `message.groups` and `message.im`.
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

Each reply appears in a thread. After that, just keep talking in the thread: follow-ups meant for the bot don't need the @mention. The first time a person asks about LinkedIn or GitHub, they get a **private** "Connect LinkedIn"/"Connect GitHub" button that nobody else in the channel can see. After connecting, they ask again.

## Replying without an @mention

The bot joins in the way a colleague would, rather than only when it's @mentioned:

| Message | What happens |
|---|---|
| `@AgentCore Assistant …` or a DM | Always answered. |
| Any other channel message | Triaged: a short Claude Haiku call reads the thread so far and picks one of four actions (below). Only a reply gets 👀 and a message. |

| Triage says | When | What the bot does |
|---|---|---|
| **Reply** | A question or request for the bot, a follow-up in its part of the thread ("the second one", "why?"), the answer to a question it asked, or a channel question it can clearly answer | Answers, like an @mention |
| **React** | A thank-you or acknowledgement to the bot ("thanks bot!") | Adds 👍 and posts nothing |
| **Correct** | Someone misstates something the bot itself posted in this thread ("so Alice has 2 PRs?" after it listed 3) | One short correction, pointing back to its message. No tools, so it never looks anything up to prove a point, and it doesn't argue or repeat itself. |
| **Ignore** | People talking to each other ("Bob, can you review #15?" and Bob's answer), chit-chat, announcements, questions only people can answer | Nothing |

Follow-ups addressed to someone else, like "@Bob can you take this?" or "Philip, can you check this?", are left to the people, even in a thread the bot has been answering in.

Triage runs in the `agent-worker` Lambda, never in the 3-second `slack-events` handler, and any error means the bot stays quiet. It uses Claude Haiku 4.5 (`triage_model_id`): Nova Micro kept answering follow-ups like "Philip do you have access to it", and Nova Lite only had to say yes or no, without the thread. Set `ASSISTANT_NAME` on the worker if you rename the bot, so triage knows which name is its own. Threads the bot is in are also remembered in the `engaged-threads` DynamoDB table (in memory locally).

### What the bot knows about the thread

Before triage and before answering, the worker reads the thread from Slack (`conversations.replies`, which the `*:history` scopes already allow). It takes the first message plus the latest 30, each cut to 2,000 characters. That thread is the bot's only memory, so:

- Everyone in a thread shares the same context. If Alice asks for her PRs and Bob asks "which one is the oldest?", the bot knows what "which one" refers to.
- An @mention after a discussion between people sees that discussion ("@bot make a ticket for this").
- A follow-up an hour later still has the context, because nothing needed to stay in memory.
- It asks one short question only when a wrong guess would matter, such as an unclear reference or creating something. It never asks for anything the thread already answers.
- What the bot looked up but didn't post (for example the full tool output) isn't remembered. Only what's in the thread is.

Because it reads every message in channels it's been invited to, the bot sends each of them, with the thread it's in, to Bedrock for triage. Messages aren't stored or logged. Only invite it to channels where that's acceptable, and use `@mention` or DMs for everything else.

## Behaviour notes

- **Profile data is posted where the question was asked.** Anyone in a public channel who can read the thread will see the answer. For private data, DM the bot.
- **The thread is the conversation history.** Everyone in a thread shares it, and it's read again from Slack for every message. Other people's messages are context only: the bot acts on the requester's accounts only because of what the requester asked.
- **Slack retries** (the `X-Slack-Retry-Num` header) are acknowledged but ignored, because the first delivery was already queued.
