# Main GitHub issue reconciliation — 2026-10-01

The human explicitly requested earlier-stage issues be handled alongside ongoing
implementation. Main read all seven open issue bodies and merged PR index, then
reviewed retained acceptance records and verified each resolved merge is an
ancestor of current accepted81e052704d712c670a0e7b66385aac91bcbe988b. Six open
items were delivered tasks whose issue state had not followed their acceptance.
Main closed them with exact evidence rather than repeating unchanged tests.

| Issue | Resolution | Qualification evidence |
|---|---|---|
| [4](https://github.com/desanv01/mirofish-research-lab/issues/4) filesystem boundaries | PR7 mergead4d9059 | Exact five-gate CI and postmerge36216243468; mandatory Linux link/traversal cases |
| [30](https://github.com/desanv01/mirofish-research-lab/issues/30) sanitized Markdown | PR33 merge7894b6a | All6CI36504074280/post36504418321;8DOM tests and seven sinks reviewed |
| [34](https://github.com/desanv01/mirofish-research-lab/issues/34) entity metadata | PR35 mergedcb6da0 | All6CI36505686164/post36506001586; real Neo4j metadata fixture |
| [40](https://github.com/desanv01/mirofish-research-lab/issues/40) project revisions/import | PR42 mergeea1200e | All7CI36510231708;20store cases including actual PG/CLI |
| [41](https://github.com/desanv01/mirofish-research-lab/issues/41) graph-read application | PR43 merge2d2c904; follow-up PR46 mergecfe4fde | All7PR/postmerge gates; cold-process SDK import defect separately corrected under issue44 |
| [45](https://github.com/desanv01/mirofish-research-lab/issues/45) retained source passages | PR47 merge9f37fdb | All7CI36574673021;44store cases including10actual PG |

[Issue3](https://github.com/desanv01/mirofish-research-lab/issues/3) stays OPEN:
bounded U01 characterization and fake-model Graphiti Community compatibility are
accepted, but combined inherited workflows, chosen semantic/live-model quality
rubric and full mandatory capability gates remain. Later accepted native/PG/
Temporal, research and playback slices contribute evidence without closing the
umbrella. Paid qualification still needs local secrets and a concrete spend cap.

All acceptance scope limitations remain. Task closure is not whole-phase or
release acceptance. No open PR remained at reconciliation; PR63 was accepted.
Main will inspect newly reported defects during each integration cycle, assign
scoped implementation in a separate project worker chat when needed, and close
only after reviewed exact-revision qualification. Avoid blanket issue closure.

## U10 review follow-up

[Issue65](https://github.com/desanv01/mirofish-research-lab/issues/65) tracks the
bounded owned cohort comparison and a defect Main found before execution:
prelaunch cancellation intent was omitted from member output and record digests.
The corrected candidate exposes authoritative `cancel_requested` independently
of terminal outcome; declared intent stays pending with no receipt or metrics.
Pure and actual PostgreSQL regressions cover that distinction. Issue65 remains
open until Main reviews exact local/hosted qualification and accepts integration.
