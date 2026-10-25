# Audit identity: what it attests

The gateway's audit line records a `user` and a `session`. They are **claims vouched for by the router**, not proof of who the person is. This note says who can name whom, and why the pilot accepts it.

## What the signature attests

The router signs the pair (`X-Forge-User`, `X-Forge-Session`) into `X-Forge-Identity`, an HMAC-SHA256 under a secret shared with the gateway alone. The gateway records the pair only when that signature verifies, and marks the line `"identity":"router"`. Any other request is recorded as `unauthenticated`, whatever it claims.

So the signature attests the router: the call came through the router, which held the secret. It does not attest the user. The router signs the name it is given and does not check it.

## Who can name whom

| Caller | Can name | Recorded as |
| --- | --- | --- |
| A runner that reaches the router | any user and session | the claimed pair, `identity: router` |
| Anything that calls the gateway directly | any user and session | `unauthenticated` |
| Anything without the shared secret | cannot produce a valid signature | `unauthenticated` |

The residual gap is the first row: a runner that reaches the router can name any user, and the router signs it.

## Why the pilot accepts it

- Closing the gap needs the router to validate a per-user token, and Omnigent v0.19.1 does not forward one to the model endpoint (see [the upstream request](upstream/omnigent-forward-user-token.md)).
- The runners are the pilot's own, inside the stack's network; the audit is for review and cost attribution, and nothing authorizes access on it.
- The signature still closes the cheaper attack: a client that is not the router cannot forge an attested identity.

Read the `user` field as "the identity the runner reported through the router", never as a verified login.

## Reopening

When Omnigent can carry the validated user token to the router, reopen validation there: check the signature against the issuer's JWKS, plus the audience, expiry and issuer, and sign the identity from the token instead of from the headers.
