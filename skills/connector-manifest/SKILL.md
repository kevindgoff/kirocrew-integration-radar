---
name: connector-manifest
description: How to write and fix Integration Radar's connector manifest (YAML) - partners, sources, connectors, surfaces and the validation rules.
triggers: ["connector manifest", "integration-radar.manifest.yaml", "add a connector", "add a partner", "manifest validation error"]
always: false
---

# Connector manifest

The connector manifest is the one file a team writes for Integration Radar. It has
two halves: `partners` (where announcements come from) and `connectors` (what the
product actually uses). It is not `app.json`, which is the Crew app manifest.

## Shape

```yaml
schema_version: 1
product: my-product
partners:
  example_api:
    display_name: Example API
    versioning: major          # major (v21), dated (v202508), semver (1.4.0), rolling
    poll: daily                # or weekly
    sources:
      - kind: sunset_schedule  # release_notes, changelog, sunset_schedule, feed,
        url: https://...       # csv_schedule, out_of_cycle, api_spec_repo
connectors:
  - id: example-reporting
    partner: example_api
    lifecycle: active          # active, legacy, retiring
    api:
      version: v21             # must match the partner's versioning
      client: example-sdk      # optional
    surfaces:
      - service: ReportService # or endpoint: /v1/reports
        resources: [report]
        fields: [metrics.clicks, segments.date]
    criticality: high          # high, medium, low
    owner: integrations-team   # optional
    hints:                     # optional, per role
      tracker_component: Reporting
```

## Validation rules

Errors are reported with line numbers, and the last valid manifest stays in use
until the file is fixed.

1. Unknown keys are errors, as are duplicate keys and YAML anchors or aliases.
2. Every connector names a defined partner and has at least one surface; a surface
   has exactly one of `service` or `endpoint`.
3. `api.version` must fit the partner's `versioning`.
4. Source URLs must be HTTPS. Their hosts are the only hosts Integration Radar will
   contact, and redirects to any other host or to plain HTTP are refused.
5. Keys that look like credentials (`token`, `secret`, `password`, `api_key`) are
   rejected anywhere. The manifest holds no secrets.
6. `legacy` and `retiring` connectors are matched like any other but flagged
   "migrate or retire".

## Getting matches right

Matching is only as good as the surfaces. List the exact field names you read or
write (`metrics.cost_micros`, not "cost"): a change that names a listed field is an
`exact` match. Pin the version you really call; a sunset of that version is always
`exact`.
