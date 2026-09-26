# Security policy

## Current support status

U00 is a source-import stage. No Research Lab release or shared deployment is supported. The inherited app remains Zep-backed and has not passed the access, request, provider and data-isolation gates. Keep any exploratory run isolated on a local machine with synthetic data; do not expose the baseline publicly. A future release policy will identify supported versions once qualified.

## Reporting a vulnerability

Use this repository's [private vulnerability reporting](https://github.com/desanv01/mirofish-research-lab/security/advisories/new) when available, or contact the repository owner privately through GitHub. Include the affected revision, reproduction steps, impact and any suggested fix. Do not put exploit details, credentials, private documents or model payloads in public issues. An issue may describe a non-sensitive symptom after coordination.

## Handling data and credentials

Use `.env.example` only as a template and keep filled `.env` files untracked. CI must use synthetic fixtures and no paid provider keys. Graph partitions alone are not authorization. Planned Graphiti/Neo4j and application access controls must be qualified before a shared service is offered. The initial model target and any spending limits are not yet configured.
