# NexaWeave runtime names and compatibility

NexaWeave is the product name for this derived project. The canonical Python
imports are `nexaweave_knowledge`, `nexaweave_storage`, and
`nexaweave_execution`; the canonical distributions are `nexaweave-knowledge`
and `nexaweave-backend`. Existing callers of the `mirofish_knowledge`,
`mirofish_storage`, and `mirofish_execution` module paths use explicit
compatibility aliases. An old distribution name is not an installation target
for the new package. The retained aliases preserve imports; they do not rename
persisted records or authorize an old runtime configuration.

## Configuration inputs

Use `NEXAWEAVE_APP_MODE`, `NEXAWEAVE_ALLOWED_ORIGINS`, and
`NEXAWEAVE_APPSTORE_DSN` for current setup. The corresponding existing inputs
`MIROFISH_APP_MODE`, `MIROFISH_ALLOWED_ORIGINS`, and
`MIROFISH_APPSTORE_DSN` remain compatibility aliases. For each pair, either
name alone is accepted; when both are present their values must be equal.
Conflicting values are rejected without echoing them. An explicitly empty
browser-origin value retains its existing meaning. Flask config subclasses may
override the mode or origin setting under either name; a legacy override in a
nearer subclass is not hidden by a canonical default in a superclass, and
conflicting definitions in the same subclass are rejected. The existing
validation, authorization and precedence boundaries remain in force.

Other `KNOWLEDGE_*` and service-specific settings retain their documented
meanings. This alias list does not imply that every historical `MIROFISH_*`
variable is a supported input. Use the current service documentation for those
settings.

## Stored and exchanged version 1 identities

Branding does not rewrite existing data or change the bytes that determine a
version 1 identity. The `mf_app` and other `mf_*` SQL schemas, migration SQL and
catalog checksums retain their recorded names. Existing UUID5 name domains,
including `mirofish:episode:v1:`, `mirofish:native-budget:v1:`,
`mirofish:connected-report-budget:v1:`, and
`mirofish-retained-import-v1:`, remain exact identity inputs. Existing graph
markers, Temporal workflow names and histories, bundle versions and kind strings
such as `mirofish_retained_sources`, hashes, and previously issued artifact
bytes retain their current values. A visual or package name change is not a
data migration, replay, or checkpoint restoration.

The original MiroFish ZIP, source mapping, inherited notices and attribution
remain available in the repository. See the root [LICENSE](../../LICENSE),
[third-party notices](../../THIRD_PARTY_NOTICES.md), and
[upstream import notes](../upstream/import-notes.md) for the source and
legal record. This page describes names only; it makes no release, provider,
quality, full-workflow, or deployment acceptance claim.
