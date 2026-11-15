# Ephemeral Jaeger chart

Disabled by default; explicit enable emits four objects in `ai-observability`: tokenless ServiceAccount, ConfigMap, ClusterIP OTLP gRPC Service and a single Deployment. Exact image/digest stays at the reviewed Jaeger 2.22.0 baseline. Config owns the namespace/network policy; selector `app.kubernetes.io/name: jaeger` matches its Collector-only ingress on TCP4317.

Memory storage is capped at 1000 traces, with memory-limiter/batch bounds and pod CPU/memory limits. Recreate strategy, restarts and eviction lose traces; this is a disposable synthetic lab profile, not durable or production storage. Query listeners remain loopback and have no Service/Ingress. No PVC, Namespace, CRD, RBAC, external service or cloud resource is emitted. Tokenless UID10001, restricted capabilities, seccomp and read-only filesystem are declared; actual admission, image startup and pressure behavior require later proof.

TCP probes test the ingestion socket, not storage health or query/backend acceptance. `python scripts/check_jaeger_chart.py --restore` performs offline Helm lint/renders and negative checks. CI separately validates generated config in the existing digest-pinned Jaeger distribution with network disabled and restricted container settings. Native config validate is recorded separately in the lab. No Helm install, cluster/Azure access or registry publication occurs. Dev/qa are offline alternatives for the same shared instance; do not apply both to one cluster.

GitOps remains pinned to the earlier Collector-only catalog until a separately reviewed promotion. Actual AKS targets, TLS/network/identity choices, backend/runtime acceptance and bootstrap handover remain pending. Terraform/APIM boundaries and old local/native files are preserved.
