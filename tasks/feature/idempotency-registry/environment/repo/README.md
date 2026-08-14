# Idempotency registry

The service layer needs an in-memory idempotency boundary for operations that return strings.
Successful results are cached by a non-empty key and the exact immutable request payload. Reusing
a key with different bytes is a conflict. Failed operations and invalid result values are never
recorded, so a later retry can execute normally.
