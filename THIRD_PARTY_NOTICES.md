# Third-party notices and source attribution

This is an initial source-level inventory, not a complete release SBOM or legal review. Release packaging will inventory exact shipped versions, license texts and corresponding-source obligations.

| Component | Current relationship | License / notice |
| --- | --- | --- |
| [MiroFish](https://github.com/666ghj/MiroFish) | All 128 archived source files imported from `MiroFish-main.zip`; snapshot commit unknown | Imported [GNU AGPL v3 license](LICENSE) and existing file notices retained. ZIP hash and mapping in [archive manifest](docs/upstream/archive-manifest.json). |
| [OASIS / CAMEL](https://github.com/camel-ai/oasis) | Declared in the inherited backend dependency set; engine behavior retained as a target | Consult exact dependency releases and notices before distribution; no relicensing implied. |
| [Graphiti](https://github.com/getzep/graphiti) | Approved target; no code imported in U00 | Apache-2.0 for the target project; exact version and notices to be recorded at integration. |
| [Neo4j Community](https://neo4j.com/open-source-project/) | Approved self-hosted database target; no binary bundled in U00 | GPLv3 Community terms; packaging and version to be reviewed. |
| [PyMuPDF / MuPDF](https://pymupdf.readthedocs.io/en/latest/about.html) | Declared in inherited backend dependencies | AGPL/commercial terms apply according to distribution/use. |
| Zep Cloud | Legacy integration remains in imported source and lockfile; not a target runtime prerequisite | Provider client and service terms require review if redistributed/used. |
| Other Python and npm dependencies | Declared in inherited locks | Each pinned-version license and notices must be inventoried for release. |

The root `LICENSE` is the unchanged inherited AGPL-3.0 text. The upstream star-history component retains its own [notice](.github/star-history/THIRD_PARTY_NOTICES.md). Replacing Zep with Graphiti does not change the inherited MiroFish license. No upstream endorsement is claimed.
