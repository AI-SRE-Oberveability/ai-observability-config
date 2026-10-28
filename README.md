# Observability configuration

Verified public repository: `AI-SRE-Oberveability/ai-observability-config`; reviewed source is published under the organization; initial receipts remain in the lab evidence.

Owns shared telemetry services and backend/monitoring configuration. `local/` contains pinned Collector/Jaeger definitions and verified-download metadata; `telemetry-contract.md` defines capture/identity expectations; `profiles/compatibility.md` keeps local, Managed and optional SaaS support distinct.

Current state: configuration drafts and Collector CLI validation only; complete backend export is unverified. Managed entitlement/version/sizing, Operator/OneAgent, dashboard/alert configuration and signal-specific API support remain plan work. Do not place application or Azure-resource ownership here.

CI in .github/workflows/ci.yaml verifies pinned image/loopback boundaries, Compose rendering and the pinned Collector configuration using its real validator. These checks do not establish running backend ingestion or Managed compatibility. CI tools are hash-locked in requirements-ci.txt.
