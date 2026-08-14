# Event feed

Events are immutable values with a unique non-empty ID, a non-negative integer creation time, and
a non-empty kind. Feed order is newest first by `(created_at, id)`. Callers need deterministic,
opaque cursor pagination without mutating the supplied sequence. The existing `list_recent`
function remains supported.
