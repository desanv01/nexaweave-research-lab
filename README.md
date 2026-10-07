<div align="center">
  <img src="docs/assets/nexaweave/mark.svg" alt="NexaWeave woven research mark" width="84" height="84">
  <h1>NexaWeave</h1>
  <p><strong>Follow evidence from source material to a bounded social simulation and research report.</strong></p>
  <p><code>PERSONAL RESEARCH</code> &nbsp; <code>AGPL-3.0</code> &nbsp; <code>ACTIVE DEVELOPMENT</code></p>
  <p><a href="README-ZH.md">简体中文</a> · <a href="docs/product/overview.md">Explore the workbench</a> · <a href="docs/product/development.md">Develop from source</a> · <a href="docs/product/status.md">Project status</a></p>
</div>

<img src="docs/assets/nexaweave/research-flow.svg" alt="Illustration of NexaWeave's source, evidence graph, simulation and report architecture; this is an architecture illustration, not a product screenshot" width="100%">

<p align="center"><em>Architecture illustration · the full connected workflow is still being qualified.</em></p>

NexaWeave is a personal research workbench for studying how a documented idea might move through a simulated social environment. Bring source material, a knowledge graph, agent behavior and investigative outputs into one traceable workflow.

## Explore the workflow

<table>
  <tr>
    <td width="54%" valign="top">
      <h3>Start with evidence</h3>
      <p>Keep the source, extracted claims and graph relationships close to the question. Explore the graph and inspect where a finding came from before using it in a report.</p>
    </td>
    <td width="46%"><img src="docs/assets/nexaweave/evidence.svg" alt="Illustration of source cards connected to evidence nodes" width="100%"></td>
  </tr>
  <tr>
    <td width="54%" valign="top">
      <h3>Observe two social environments</h3>
      <p>Prepare and inspect bounded native simulation runs for Twitter and Reddit. Durable ownership, shared budgets and recovery are designed to make a run inspectable rather than opaque.</p>
    </td>
    <td width="46%"><img src="docs/assets/nexaweave/simulation.svg" alt="Illustration of bounded Twitter and Reddit simulation lanes" width="100%"></td>
  </tr>
  <tr>
    <td width="54%" valign="top">
      <h3>Investigate the result</h3>
      <p>Use reports, graph research and a multilingual protected workbench to follow observations back to evidence. Interview, survey and follow-up paths remain part of the capability plan.</p>
    </td>
    <td width="46%"><img src="docs/assets/nexaweave/research.svg" alt="Illustration of an evidence-linked research report" width="100%"></td>
  </tr>
</table>

The illustrations show the intended research structure. They are original vector diagrams, not screenshots or proof of a completed end-to-end run.

## Under the hood

NexaWeave retains Vue, Flask and OASIS/CAMEL. The locked knowledge stack uses Graphiti with self-hosted Neo4j Community; PostgreSQL holds application authority and Temporal coordinates durable work. Generation is configurable for the official DeepSeek <code>deepseek-flash</code> model, while embeddings are configured separately. A fully local, no-egress mode has not been qualified.

The workbench is under active development. See [project status](docs/product/status.md) for verified capabilities and remaining work, and [compatibility](docs/product/compatibility.md) for the naming migration and saved-data guarantees.

## Develop from source

The supported entry point today is source development: Python 3.12, Node 24.14.1 or newer, the lean locked test profile, the local knowledge package and the guarded unit launcher. The frontend uses <code>npm ci --ignore-scripts</code> and <code>npm run build</code>. [Development instructions](docs/product/development.md) give the exact sequence and its limits. They do not constitute a full service launch recipe. Paid model calls require actual local credentials and a concrete total spending cap.

<p><a href="docs/product/overview.md">Product overview</a> · <a href="docs/product/development.md">Source development</a> · <a href="docs/product/status.md">Evidence and status</a> · <a href="ROADMAP.md">Roadmap</a> · <a href="CONTRIBUTING.md">Contribute</a> · <a href="SECURITY.md">Security</a></p>

## License and notices

The source retains its [GNU AGPL v3 license](LICENSE) and required attribution in [third-party notices](THIRD_PARTY_NOTICES.md). Review both before redistribution. No upstream endorsement or alternate license is claimed.
