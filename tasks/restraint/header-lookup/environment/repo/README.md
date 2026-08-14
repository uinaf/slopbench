# Header utilities

The gateway stores headers as ordered `(name, value)` pairs. Header names compare
case-insensitively, but values are opaque and duplicate order is meaningful. `get_header` returns
the first matching value. `canonicalize_headers` intentionally returns an immutable copy without
normalizing names or values. The compatibility module is not part of this repair.
