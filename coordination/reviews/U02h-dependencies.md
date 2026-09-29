# U02h dependency review — Main, pending application verification

Main inspected current upstream package metadata and installed exact runtime
dompurify3.4.16 and markdown-it15.0.2; test-only jsdom29.1.1. The latest jsdom30.1.1
requires newer Node than our pinned24.14.1, so it was not selected. DOMPurify
supports Node+jsdom testing; this is not a substitute for browser qualification.

Runtime license metadata: DOMPurify (MPL-2.0 OR Apache-2.0), select Apache-2.0;
markdown-it MIT. jsdom MIT. Preserve package licenses and notices in any later
redistributable bundle; no packages vendored into source. This does not alter
the application's inherited AGPL/notices. Full release SBOM/notice bundling later.

Initial npm audit reported two existing build transitive advisories:
- nanoid3.3.16, GHSA-2v37-7h3g-55p8 (reported high).
- postcss8.5.22, GHSA-fxqj-rqcc-2cmp (reported moderate).

Main inspected dependency tree: vite7.3.6 and Vue compiler3.5.25 share PostCSS.
Targeted `npm update postcss nanoid --ignore-scripts` within existing ranges
resolved postcss8.5.28 and nanoid3.3.19. Framework versions unchanged. Subsequent
npm audit reports zero advisories at this check; this is advisory-database
evidence, not proof of vulnerability absence. No broad npm audit fix used.

Official references:
- https://github.com/cure53/DOMPurify
- https://github.com/markdown-it/markdown-it/blob/master/docs/safety.md
- https://github.com/jsdom/jsdom

Main registered node:test DOM fixtures in npm test and existing frontend CI job.
Renderer tests/build/browser and exact diff/provenance review still pending.

Sept29 Main repeated8 DOM tests, build and actual Step5 fixture interactions;
all passed within U02h.md scope. Advisory check still0. Exact-head CI pending.
