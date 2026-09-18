# GitHub and AgentCore Identity Setup

The agent's GitHub tool (`use_github` in `src/github.py`) gets the signed-in user's own GitHub
access token from AgentCore Identity, then hands that token to GitHub's official **remote MCP
server** (`https://api.githubcopilot.com/mcp/`) as a Bearer credential. A small nested Strands
agent (Claude Haiku, via `GITHUB_MODEL_ID`) is given that server's tools for the duration of the
call, so the assistant can act on issues, pull requests, repositories, code search, and org/team
membership — not just read the user's profile — scoped to whatever the GitHub App's permissions
and the org's installation allow.

`fetch_token` still requests OAuth scopes (`repo`, `read:user`, `read:org`) for historical reasons
(this credential used to be a classic OAuth App), but a GitHub App's user-to-server tokens ignore
OAuth scopes entirely — access is governed by the App's configured **permissions** instead (see
step 1 below). The scope list is effectively inert; what actually gates access is the App's
permission grants plus whether the org has installed the App at all.

The AgentCore Identity half of this follows the exact same pattern as
[linkedin-setup.md](linkedin-setup.md) — a second, independent AgentCore Identity OAuth2
credential provider, using the built-in `GithubOauth2` vendor instead of `LinkedinOauth2`. No
AgentCore Gateway is involved; the agent talks directly to GitHub's public remote MCP server,
authenticated with the per-user token from AgentCore Identity's vault.

**The credential is a GitHub App, not a classic OAuth App.** A classic OAuth App requires every
organization owner to separately approve it under **OAuth App access restrictions**, regardless of
granted scopes — and if the app is owned by an account outside your GitHub Enterprise Managed
Users (EMU) enterprise, some org owners can't approve it at all under enterprise policy. A GitHub
App sidesteps both problems: organizations opt in by **installing** the app (a different,
EMU-friendly policy gate than OAuth App restrictions), which is a one-time action per org instead
of a per-user consent bottleneck. This app uses the GitHub App's **user-to-server** OAuth flow
(not installation/server-to-server tokens), so every Slack user still gets their own GitHub token
scoped to what they personally can see — installation tokens have no user identity and would break
"my issues"/"my PRs"/"my orgs" style questions.

## 1. Create a GitHub App

1. Go to <https://github.com/settings/apps> under an account **inside your EMU enterprise**
   (an organization, not a personal developer account — see above) and choose **New GitHub App**.
2. Fill in an app name and homepage URL (anything reasonable — they're not used by the flow).
3. Leave **Callback URL** blank for now; you'll set it in step 3 below, once the credential provider exists and prints it. Leave **Webhook** unchecked (not used).
4. Under **Permissions → Repository permissions**, grant **Contents**, **Issues**, and **Pull requests** (**Metadata: Read-only** is included automatically). Use **Read-only** for a read-only assistant; use **Read and write** on all three if you want the bot to open issues/PRs too (see "Enabling write access" below) — Contents needs write as well, since creating a PR means creating a branch and committing files. If org/team-membership questions need it, also grant **Organization permissions → Members: Read-only**, though note `get_teams` (the tool the assistant uses for this) only ever sees orgs where the user is on a Team, regardless of this permission — see [architecture.md](architecture.md) for the underlying limitation.
5. Choose who can install the app (your own EMU orgs, or any org if you plan to onboard others later — this is independent of the OAuth App restriction policy the previous approach was blocked by).
6. After creating the app, copy the **Client ID** from the app's General settings, then generate and copy a **Client secret** into `.env`:
   ```bash
   GITHUB_CLIENT_ID=...
   GITHUB_CLIENT_SECRET=...
   ```
   You do not need the app's private key — that's only for installation (server-to-server) tokens, which this setup doesn't use.

## 2. Create the AgentCore credential provider

```bash
set -a; source .env; set +a
make identity-github        # infra-as-code/scripts/identity-setup.sh github-provider
```

The script:
- creates or updates the OAuth2 credential provider `slack-agent-github` using the **built-in `GithubOauth2` vendor** (its `github.com/login/oauth/*` endpoints are the same for OAuth Apps and GitHub Apps — only the Client ID/Secret registration differs);
- passes the client secret through a private temp file rather than on the command line, and never through Terraform. AgentCore stores it in Secrets Manager under `bedrock-agentcore-identity!default/oauth2/slack-agent-github`;
- prints the provider's **redirect URL**.

As with LinkedIn, the provider is managed by this script rather than Terraform, to keep the client secret out of Terraform state.

## 3. Register the redirect URL with GitHub

In the GitHub App's **General** settings, set **Callback URL** to the URL the script printed:

```
https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/callback/<uuid>
```

GitHub redirects to **AgentCore Identity** (not to your app). AgentCore exchanges the code for a token, then sends the browser on to your app's `/oauth2/callback` — the same callback endpoint LinkedIn uses; it's provider-agnostic.

To print the URL again later: `./infra-as-code/scripts/identity-setup.sh show`.

## 4. Install the app on each organization

Each org owner installs the app once, from its public page (`https://github.com/apps/<app-slug>`) or an org-specific install link, choosing which repositories it can access. This replaces the old "approve the OAuth App" step and is a **one-time action per org** — there's no per-user or per-scope re-approval as this app evolves.

## 5. Allow-list your app's return URL

This is shared infrastructure — the same **workload identity** allow-list that LinkedIn uses already covers GitHub too, since `allowed_oauth2_return_urls` is a property of the workload identity, not of any one credential provider. If you've already completed LinkedIn setup (or run `make local-workload` / `terraform apply`), there's nothing further to do here.

| Environment | Workload identity | Return URL | Set by |
|---|---|---|---|
| Local | `slack-agent-local` | `http://localhost:8081/oauth2/callback` | `make local-workload` |
| AWS | created automatically with the Runtime | `https://<api>.execute-api.<region>.amazonaws.com/oauth2/callback` | Terraform (`terraform_data.allowed_return_urls`) |

## Enabling write access (opening issues/PRs from Slack)

By default the App is read-only. To let the bot create issues and pull requests:

1. On the GitHub App, change **Contents**, **Issues**, and **Pull requests** from Read-only to **Read and write**.
2. Every org that already installed the app must have an owner **re-accept the permission upgrade** — GitHub prompts for this automatically (in the org's installed-apps settings, or on the app's next use) since it's a scope increase, similar in spirit to the old `read:org` scope-bump rollout but gated by install-acceptance rather than OAuth App approval.
3. No further code changes are needed: `use_github` passes through whatever tools GitHub's remote MCP server exposes for the token's permissions, so `create_issue`/`create_pull_request`/etc. simply become available once the App and the org both allow it.

**Safety behavior:** the nested GitHub agent (see `GITHUB_AGENT_SYSTEM_PROMPT` in `src/github.py`) always drafts the exact write action — repo, branch, title, body/content — and asks the user to confirm in Slack *before* calling a create/update/merge/delete tool; it never writes on the first pass. Because the nested agent is stateless between calls, the outer assistant (`SYSTEM_PROMPT` in `src/main.py`) is responsible for relaying that draft and, on confirmation, restating the complete details back to `use_github` — there's no separate "pending action" store.

## Rotating the GitHub secret

Put the new secret in `.env` and run `make identity-github` again. The script updates the existing provider in place. Users' existing tokens keep working until they expire.

**Swapping to a different app entirely** (e.g. this migration, from a classic OAuth App to a GitHub App) invalidates every previously stored token, since it's tied to the old app's Client ID — everyone will see `AUTHORIZATION_REQUIRED` once and needs to reconnect, same as any other scope/provider change above.

GitHub App user-to-server tokens also **expire** (8 hours by default, refreshed automatically via AgentCore Identity), unlike classic OAuth App tokens which didn't — users may see reconnect prompts somewhat more often than before.

## Revoking a user's access

- **The user** can revoke the app at GitHub → Settings → Applications → Authorized GitHub Apps. The next call gets a 401, and the tool automatically asks them to connect again (`forceAuthentication=True`).
- **An org owner** can uninstall the app from their organization at any time, which revokes access for every member of that org.
- **Everyone:** delete and recreate the credential provider, which invalidates every stored token.
