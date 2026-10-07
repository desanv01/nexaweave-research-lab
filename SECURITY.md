# Security policy

## Current support

NexaWeave is public source in active development, with no qualified release or public app deployment. The complete access, provider, data isolation and operational security gates remain open. Use synthetic data for development checks. Do not expose an exploratory service to others or submit private documents.

## Report a vulnerability

Use [private vulnerability reporting for this repository](https://github.com/desanv01/nexaweave-research-lab/security/advisories/new) when available. If that route is unavailable, contact the repository owner privately through GitHub. Include the affected revision, steps to reproduce, impact and a suggested fix if known. Keep exploit details, credentials, private documents and model payloads out of public issues.

## Protect data and credentials

Treat .env.example as a template and keep filled .env files untracked. Development checks use synthetic fixtures; they do not need paid provider keys. Graph partitions alone do not grant authorization. Paid model calls require actual local credentials and a concrete total spending cap. A fully local, no-egress mode and public deployment require separate qualification.
