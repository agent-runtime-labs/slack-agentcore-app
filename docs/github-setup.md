# GitHub and AgentCore Identity Setup

The agent's GitHub tool (`use_github` in `src/github.py`) gets the signed-in user's own GitHub
access token from AgentCore Identity (OAuth2 scopes `repo`, `read:user`, and `read:org`), then
hands that token to GitHub's official **remote MCP server** (`https://api.githubcopilot.com/mcp/`)
as a Bearer credential. A small nested Strands agent (Claude Haiku, via `GITHUB_MODEL_ID`) is
given that server's tools for the duration of the call, so the assistant can act on issues, pull
requests, repositories, code search, and org/team membership — not just read the user's profile —
all scoped to whatever the user consented to.

`read:org` was added after `repo`/`read:user`. Every call to `use_github` requests all three
scopes up front (see `fetch_token` in `src/github.py`), so a user who connected before this change
will see `AUTHORIZATION_REQUIRED` on their very next GitHub request — not just org/team ones — and
needs to reconnect once before anything works again.

The AgentCore Identity half of this follows the exact same pattern as
[linkedin-setup.md](linkedin-setup.md) — a second, independent AgentCore Identity OAuth2
credential provider, using the built-in `GithubOauth2` vendor instead of `LinkedinOauth2`. No
AgentCore Gateway is involved; the agent talks directly to GitHub's public remote MCP server,
authenticated with the per-user token from AgentCore Identity's vault.

## 1. Create a GitHub OAuth App

1. Go to <https://github.com/settings/developers> (or your organization's **Settings → Developer settings**) and choose **OAuth Apps → New OAuth App**.
2. Fill in an application name and homepage URL (anything reasonable — they're not used by the flow).
3. Leave **Authorization callback URL** blank for now; you'll set it in step 3 below, once the credential provider exists and prints it.
4. After creating the app, copy the **Client ID**, then generate and copy a **Client secret** into `.env`:
   ```bash
   GITHUB_CLIENT_ID=...
   GITHUB_CLIENT_SECRET=...
   ```

## 2. Create the AgentCore credential provider

```bash
set -a; source .env; set +a
make identity-github        # infra-as-code/scripts/identity-setup.sh github-provider
```

The script:
- creates or updates the OAuth2 credential provider `slack-agent-github` using the **built-in `GithubOauth2` vendor**;
- passes the client secret through a private temp file rather than on the command line, and never through Terraform. AgentCore stores it in Secrets Manager under `bedrock-agentcore-identity!default/oauth2/slack-agent-github`;
- prints the provider's **redirect URL**.

As with LinkedIn, the provider is managed by this script rather than Terraform, to keep the client secret out of Terraform state.

## 3. Register the redirect URL with GitHub

In the GitHub OAuth App, set **Authorization callback URL** to the URL the script printed:

```
https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/callback/<uuid>
```

GitHub redirects to **AgentCore Identity** (not to your app). AgentCore exchanges the code for a token, then sends the browser on to your app's `/oauth2/callback` — the same callback endpoint LinkedIn uses; it's provider-agnostic.

To print the URL again later: `./infra-as-code/scripts/identity-setup.sh show`.

## 4. Allow-list your app's return URL

This is shared infrastructure — the same **workload identity** allow-list that LinkedIn uses already covers GitHub too, since `allowed_oauth2_return_urls` is a property of the workload identity, not of any one credential provider. If you've already completed LinkedIn setup (or run `make local-workload` / `terraform apply`), there's nothing further to do here.

| Environment | Workload identity | Return URL | Set by |
|---|---|---|---|
| Local | `slack-agent-local` | `http://localhost:8081/oauth2/callback` | `make local-workload` |
| AWS | created automatically with the Runtime | `https://<api>.execute-api.<region>.amazonaws.com/oauth2/callback` | Terraform (`terraform_data.allowed_return_urls`) |

## Rotating the GitHub secret

Put the new secret in `.env` and run `make identity-github` again. The script updates the existing provider in place. Users' existing tokens keep working until they expire.

## Revoking a user's access

- **The user** can revoke the app at GitHub → Settings → Applications → Authorized OAuth Apps. The next call gets a 401, and the tool automatically asks them to connect again (`forceAuthentication=True`).
- **Everyone:** delete and recreate the credential provider, which invalidates every stored token.
