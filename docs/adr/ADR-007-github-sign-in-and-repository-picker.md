# ADR-007 — Connect GitHub by signing in and picking a repository

- **Status:** accepted; implemented and tested locally, not yet exercised against real GitHub
- **Date:** 2026-09-23

## Context

Connecting a site's repository used to take a GitHub App that each workspace registered itself
(app ID, slug, private key under Keys), then an `owner/name` typed into a form, then a trip to
GitHub's install page. The callback bound whatever `installation_id` GitHub appended to the URL.

On 2026-09-23 the deployment's own app (`seo-autopilot-oryxen`) was made public so any workspace
can install it. That makes the old callback unsafe as a platform default: anyone can put anyone's
installation id in that URL, and the app's key can mint a token for every installation of it. It
also let a person with read-only access to an organisation's repository make the app write to it.

## Decision

The connect flow is the one Claude and ChatGPT use:

1. **Connect GitHub** sends the person to GitHub's OAuth authorize page for the app (`client_id`,
   our callback, and a random single-use `state` bound to tenant, site and actor, 30 minutes).
2. The callback exchanges the code for the person's user-to-server token and asks GitHub, as that
   person, which installations of this app they can reach (`GET /user/installations`) and which
   repositories in each they can push to (`GET /user/installations/{id}/repositories`, filtered on
   the person's own `permissions.push`/`admin`, archived excluded). With no installation they are
   sent to the app's install page on the same state; GitHub returns them to the same callback
   (registered as the Setup URL) and they are signed in again. The round trip is capped at three.
3. The token is then dropped. Only the list is kept, on a second state row that is bound to the
   same actor and site and expires after 15 minutes.
4. A dialog lists those repositories. The pick is sent by GitHub's numeric id, looked up in that
   list, and re-checked as the app (installation permissions and repository grant) before the
   connector is bound. A stored access token on the site is revoked at the same moment.

The callback never reads `installation_id`. The typed-repository install endpoint
(`POST /v1/sites/{id}/connectors/github/installation`) is removed.

The app's OAuth half (`GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, or `client_id` and
`client_secret` on a workspace's own app under Keys) is required. Without it the flow refuses with
`github_app_sign_in_not_configured`; it does not fall back to trusting a URL. A workspace that saved
the deployment's own app under Keys before this borrows the deployment's OAuth half, because it is
the same app.

## Consequences

- No user token and no installation token is ever stored. Deploys still mint a one-hour
  installation token per run, as before.
- Personal access tokens stay available under "Use an access token instead", and a workspace's own
  app stays available under Keys, for organisations that will not install a third-party app.
- API: additive `POST .../github/authorize`, `GET .../github/repositories`,
  `POST .../github/repository`; `POST .../github/installation` removed (only the web app called it).
  No migration: both steps reuse `connector_oauth_state`. Old in-flight install states are refused
  as `installation_state_invalid` because their scopes do not match.
- Rollback: redeploy the previous build. Connectors bound by either flow have the same shape
  (`provider_key = github_app`, `installation_id` in `config_json`), so none need changing.

## GitHub App settings this depends on

- Callback URL (OAuth redirect) and Setup URL both set to `GITHUB_APP_CALLBACK_URL`.
- "Redirect on update" on. "Request user authorization (OAuth) during installation" off, because
  GitHub disables the Setup URL when it is on.
- A client secret generated and placed in the deployment's environment.
