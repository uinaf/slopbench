# Runtime configuration overlays

Configuration is an ordinary string-keyed mapping. Runtime operators may override only
`timeout_seconds` through `APP_TIMEOUT_SECONDS` and `log_level` through `APP_LOG_LEVEL`.
Unrecognized environment variables, including other `APP_` names, are ignored. The input mapping
is never mutated and existing unrelated configuration remains intact.
