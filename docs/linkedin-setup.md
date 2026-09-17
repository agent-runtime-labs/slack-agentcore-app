# LinkedIn and AgentCore Identity Setup

The agent's only tool reads the signed-in user's profile from `https://api.linkedin.com/v2/userinfo`, using OpenID Connect scopes `openid profile email`.

## 1. Create a LinkedIn developer app

1. Go to <https://developer.linkedin.com/> and choose **Create app**.
2. LinkedIn requires a **Company Page**. Create a placeholder page if you don't have one.
3. Under **Products**, request **Sign In with LinkedIn using OpenID Connect**. It's usually approved instantly.
4. Under **Auth**, copy the **Client ID** and **Primary Client Secret** into `.env`:
   ```bash
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
   ```

## 2. Create the AgentCore credential provider

```bash
set -a; source .env; set +a
make identity        # infra-as-code/scripts/identity-setup.sh linkedin-provider
```

The script:
- creates or updates the OAuth2 credential provider `slack-agent-linkedin` using the **built-in `LinkedinOauth2` vendor**;
- passes the client secret through a private temp file rather than on the command line, and never through Terraform. AgentCore stores it in Secrets Manager under `bedrock-agentcore-identity!default/oauth2/slack-agent-linkedin`;
- prints the provider's **redirect URL**.

The provider is managed by this script rather than Terraform because the Terraform AWS provider (v6.64) has no LinkedIn-specific block. Using the script also keeps the secret out of Terraform state.

## 3. Register the redirect URL with LinkedIn

In the LinkedIn app, go to **Auth → OAuth 2.0 settings → Authorized redirect URLs** and add the URL the script printed:

```
https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/callback/<uuid>
```

LinkedIn redirects to **AgentCore Identity** (not to your app). AgentCore exchanges the code for a token, then sends the browser on to your app's `/oauth2/callback`.

To print the URL again later: `./infra-as-code/scripts/identity-setup.sh show`.

## 4. Allow-list your app's return URL

AgentCore Identity only redirects the browser to URLs listed on the **workload identity**:

| Environment | Workload identity | Return URL | Set by |
|---|---|---|---|
| Local | `slack-agent-local` | `http://localhost:8081/oauth2/callback` | `make local-workload` |
| AWS | created automatically with the Runtime | `https://<api>.execute-api.<region>.amazonaws.com/oauth2/callback` | Terraform (`terraform_data.allowed_return_urls`) |

## Rotating the LinkedIn secret

Put the new secret in `.env` and run `make identity` again. The script updates the existing provider in place. Users' existing tokens keep working until they expire.

## Revoking a user's access

- **The user** can revoke the app at LinkedIn → Settings → Data privacy → Permitted services. The next call gets a 401, and the tool automatically asks them to connect again (`forceAuthentication=True`).
- **Everyone:** delete and recreate the credential provider, which invalidates every stored token.
