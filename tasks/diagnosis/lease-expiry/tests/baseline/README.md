# In-memory lease registry

The registry coordinates exclusive work by lease name. A lease is active only while
`expires_at > now`; the exact expiry instant is no longer owned. Acquisition and renewal use the
explicit integer clock supplied by the caller. A successful operation sets expiry to `now + ttl`.
Failed operations leave registry state unchanged.

The first incident concerns exact-boundary acquisition. A follow-up review covers stale-owner
renewal after the initial repair.
