# Retained source to knowledge ingestion plan

The bridge connects an owned `mf_app` project and retained source revision to an existing `mf_knowledge` scope binding. It creates an offline, validated plan; plan export never calls a model or mutates a graph. Both schemas must already have been explicitly migrated. The trusted host supplies the authenticated principal, display graph ID, retained source revision UUID, operation UUID, and validated ontology. The CLI does not accept source text, hashes, timestamps, evidence IDs, workspace overrides, or alternate scopes.

## Offline plan

After importing a project and source text, and binding a source-layer graph through the trusted host, set `NEXAWEAVE_APPSTORE_DSN` locally and run:

```text
python -m nexaweave_storage export-ingestion --principal owner --display-graph-id graph_1 --source-revision 44444444-4444-4444-4444-444444444444 --operation-id 55555555-5555-5555-5555-555555555555 --ontology ontology.json --output new-plan.json
```

The ontology file is an explicitly named, bounded, regular JSON file. Output is stdout by default or an exclusively created new file. The JSON includes the full retained source text, so it is sensitive local data, and marks `ingestion_executed: false`. It contains the selected scope, exact source envelope, ontology, canonical request fingerprint, and retained-source provenance. Export neither creates a binding nor migrates a schema. It does not authorize a live provider call.

`SourceIngestionBridge(connection_factory).plan(principal, display_graph_id, source_revision, operation_id, ontology)` resolves the immutable binding and rejects tombstoned, non-source, run, and branch scopes. It reads the project through its owner and checks that project and workspace UUIDs match the scope. It then uses `SourceStore.get_source` to revalidate retained bytes, SHA-256, passages, and offsets. At least one passage is required. The envelope uses the entire exact retained text, all evidence IDs in stored order, the source revision/name/hash/server recorded time, and the supplied operation UUID. It has `source_kind=document`, the ontology's revision, and no inferred asserted-valid time. The existing envelope limit is 32,768 Unicode codepoints; longer retained text is refused rather than truncated. Chunking will require a separate explicit design.

## Host-only dispatch

`await bridge.dispatch(principal, display_graph_id, source_revision, operation_id, ontology, coordinator, authorize_model_call=callback)` rebuilds the plan and calls the injected existing `KnowledgeIngestionCoordinator` only when the trusted callback returns exactly `True`. The default is denied. The callback is a host dependency, not a CLI or client field. A successful coordinator receipt is revalidated against the scope, operation, fingerprint, and evidence IDs. The coordinator owns operation admission, idempotence, and uncertain outcomes. The bridge does not instantiate Graphiti, a model client, or a provider.

Binding ownership checks do not enforce a spending cap. Before live integration, the trusted host must implement durable budget authorization and secret configuration and inject a coordinator backed by the chosen provider. This packet includes neither a paid endpoint nor provider bootstrap. The source SHA-256 is over the entire retained extracted document; a downstream `SourceEnvelope` or other passage-level submission may have a different content hash. Resolvable passage IDs establish an exact substring of retained text, not the truth of a claim or fidelity to an original PDF/page.
