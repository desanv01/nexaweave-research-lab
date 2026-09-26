# Persisted filesystem boundaries (U02a)

Projects, simulations, and reports use `app.utils.safe_paths` to validate
resource IDs and resolve targets under their configured storage root. IDs are
single ASCII components, at most 128 characters, using letters, digits,
underscores, hyphens, and internal dots. Existing `proj_`, `sim_`, and
`report_` identifiers remain valid. Windows device names, separators, colons,
controls, dot segments, trailing dots or spaces, and absolute paths are
rejected. Fixed filenames and section indices are checked through the same
boundary.

The resolver checks the final target as well as existing parent components.
Existing symlink, junction, and reparse-point descendants are rejected,
including links to sibling resources inside the same root. Directory listings
skip invalid entries. A missing
simulation read does not create its directory; writes explicitly create a
validated directory. Legacy flat report JSON and Markdown paths remain
supported after ID validation. Recursive project and report deletion checks
existing descendants before deletion. API path errors return the stable
`{"success": false, "error": "Invalid resource path"}` payload with HTTP
400.

This boundary does not provide universal race-proof sandboxing. A local actor
who can change symlinks or junctions concurrently with a validated open,
write, or delete may race the check. Storage roots and descendants must be
protected against untrusted local mutation. U02 still requires separate
authentication, upload, rendering, and broader security review before any
public exposure.
