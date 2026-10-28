# Profile compatibility matrix

| Capability | local-otel | managed-lab | saas-trial |
| --- | --- | --- | --- |
| Deterministic policy workload | Implemented | Reuse after deployment validation | Reuse if authorized |
| OTel SDK spans | Local validation scope | Confirm endpoint, auth, enrichment and version | Separate endpoint and entitlement check |
| Native OneAgent path | Not represented | Preferred experiment; version and deployment mode pending | Separate comparison |
| Collector traces | Local Jaeger path | Verify signal independently | Verify signal independently |
| Metrics | Local Prometheus exposition | Verify supported ingestion/API and licence | Confirm enabled capabilities |
| Logs | Correlated local JSON only | Ingestion, retention and query API pending | Separate ingestion/Grail setup pending |
| AI dashboards | No vendor AI app | Supported classic surfaces after version check | Do not assume trial enables AI app/Grail |
| SRE adapter | Planned | Verified supported read APIs only | DQL/Grail only if separately supported |
| reference enterprise environment transferability | Synthetic engineering patterns only | Requires observed versions and experiment evidence | SaaS-specific findings clearly labelled |

Managed gate: licence/evaluation and installer entitlement, cluster release, compatible OneAgent/ActiveGate/Operator, approved OS/node count/storage/IOPS, DNS/TLS and connectivity, each telemetry endpoint, monitoring entitlements, retention, backups/upgrades and a total cost envelope. No free Managed edition is assumed. No backend VM is provisioned by this slice.

The handoff's vendor claims must be rechecked against current official sources at integration time. Local backend success never closes this gate.
