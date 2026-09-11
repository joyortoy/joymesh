# Subscription and funding verification

Funding evidence is normalized as subscription, paid API, local compute, or
unknown, with confirmed, inferred, or unknown confidence. JoyMesh uses official
harness status output or provider APIs when available. It does not scrape
billing pages or guess from the presence of a credential.

Unknown funding receives a routing uncertainty penalty. Exhausted subscriptions
are rejected, concurrency and quota reserves are preserved, and alternatives
are explained. A subscription route never silently becomes paid API inference.

Paid fallback policy defaults to `ASK`. `NEVER` rejects it; `ALLOW WITH LIMITS`
requires explicit monetary, token, and concurrency limits.
