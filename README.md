# Observability configuration

Verified public repository: `AI-SRE-Oberveability/ai-observability-config`; reviewed source is published under the organization; initial receipts remain in the lab evidence.

Owns shared telemetry services and backend/monitoring configuration. `local/` contains pinned Collector/Jaeger definitions and verified-download metadata; `telemetry-contract.md` defines capture/identity expectations; `profiles/compatibility.md` keeps local, Managed and optional SaaS support distinct.

Current local review profile: pinned native Windows Collector/Jaeger validation and a real deterministic ingest/query cohort passed, with exported-data canaries and owned-process cleanup recorded in the lab. The native profile remains on a review branch until merge; Compose remains separately unproved. Managed entitlement/version/sizing, Operator/OneAgent, dashboard/alert configuration and signal-specific API support remain plan work. Do not place application or Azure-resource ownership here.

CI in .github/workflows/ci.yaml verifies pinned image/loopback boundaries, Compose rendering and the pinned Collector configuration using its real validator. These checks do not establish running backend ingestion or Managed compatibility. CI tools are hash-locked in requirements-ci.txt.

The native profiles under local/native bind every listener to loopback and preserve the same capture processors. scripts/check_config.py checks those source boundaries; the lab runner adds private bounded file captures for proof only. No backend credentials or raw captures belong here.
