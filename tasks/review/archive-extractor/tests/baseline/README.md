# Archive extractor

`extract_archive` expands a trusted ZIP container whose member names and contents remain
untrusted. It must reject any member that escapes the destination, enforce the 256 KiB member and
1 MiB archive limits, and leave no extracted files when validation or extraction fails. The caller
provides an empty destination it owns; replacement of existing files is outside the contract.

Directory entries need no returned path. Regular files are returned in archive order. Streaming
copy is required; loading an entire member into memory is not.
