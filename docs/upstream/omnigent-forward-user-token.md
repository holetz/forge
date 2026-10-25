# Upstream request (draft, not filed): forward the caller's token to the model endpoint

Status: **draft**. Nothing here has been filed. Opening an issue on the upstream project is left to the maintainer of this repository.

Target: Omnigent (v0.19.1 was read).

## Title

Allow the model client to send a per-request, per-user credential or header

## Summary

When Omnigent runs behind an OIDC login, the model endpoint cannot tell which user made a call. A gateway placed between Omnigent and the model provider (routing, budgets, audit) can only see the one static key Omnigent holds. We ask for a way to carry the validated user's identity token, or a token exchanged for it, to the model base URL on each request.

## What we observed (v0.19.1, read from source, no container run)

- `omnigent/inner/open_responses_sdk.py` builds the model client as `OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)` from the process environment: one static key, and no per-user or per-request header hook.
- `omnigent/server/routes/auth.py` (`_validate_id_token`) validates the IdP `id_token` at the callback and then discards it.
- The session is a cookie JWT minted by Omnigent itself (`omnigent/server/oidc.py`, `mint_session_token`, signed with `OMNIGENT_OIDC_COOKIE_SECRET`). It is not verifiable against the issuer's JWKS and is never sent to the model endpoint.

## Request

One of:

1. **Per-request headers.** A setting (for example a list of header templates) that adds headers to each model call, filled from the session: the user's `sub`/email, and optionally the validated `id_token` or an access token.
2. **A hook on model calls.** A documented extension point invoked before each model request, receiving the session and returning extra headers or an `api_key` for that call.

Either should be off by default and should never log the token.

## Why

- A gateway can then validate the token (issuer signature, audience, expiry) and attribute cost and audit to a verified user rather than to a name the caller reports.
- Without it, an operator who needs verified attribution must patch or fork Omnigent, or give the gateway a secret that can mint any user's session.

## Alternatives we rejected

- Forking Omnigent: breaks the thin-adaptation rule of the adopting project.
- Verifying Omnigent's cookie at the gateway with the shared cookie secret: needs the same hook to deliver the cookie, and the secret mints any user's session.

## Offer

Happy to test a branch or a release candidate against a local OIDC provider and report back.
