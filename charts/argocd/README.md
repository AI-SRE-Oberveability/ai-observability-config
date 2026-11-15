# Locked Argo dependency scaffold

This wrapper is disabled by default and is not deployable. Its real Helm-generated Chart.lock selects upstream argo-cd 10.18.0 / Argo CD 3.6.1. `locks/dependencies.json` also verifies the complete upstream archive and any bundled transitive archives. Namespace/CRD source belongs to config; both are excluded and checked in the explicitly enabled smoke render. Secret values/controller installation are not supplied.

Argo 3.6.1's upstream toolchain selects Helm 4.3.1 and Kustomize 5.8.3. These intentional private pins do not upgrade global tools. Windows/Linux archive/executable hashes are locked; Kubernetes 1.35.0 is an offline API baseline, not a verified/approved AKS target.

With this repo's existing hash-locked Python CI environment:

```text
python scripts/check_dependencies.py --restore
python scripts/check_dependencies.py
```

The first command explicitly restores checksum-locked public inputs to the parent's `.runtime/chart-inputs` or `CHART_INPUT_CACHE`; the second works from that cache offline. Rendering uses private staging; tools/archives are not versioned source. Default rendering emits zero objects. The enabled smoke verifies dependency coherence and no Namespace/CRD emission, not controller security, image digest readiness, connectivity or adoption. Do not install/promote this scaffold.

Existing Collector/Jaeger local/native profiles and image/package locks are preserved. Platform/application charts, GitOps releases and handover checks remain later P2.13 work.
