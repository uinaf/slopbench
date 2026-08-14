# Query-aware response cache

This fixture contains a small in-process HTTP response cache. Cache identity lowercases the URL
scheme and authority, uses `/` for an empty path, preserves the raw path and query bytes, and
ignores fragments. Query order and duplicate parameters are significant because upstream services
may interpret them differently. Only absolute HTTP and HTTPS URLs without user information are
accepted.

The public regression reproduces an incident where two paginated URLs returned the same cached
response.
