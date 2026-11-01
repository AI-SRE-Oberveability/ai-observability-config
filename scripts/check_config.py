"""Validate source-level local telemetry deployment boundaries."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    compose = yaml.safe_load((ROOT / "local/compose.yaml").read_text())
    collector = yaml.safe_load((ROOT / "local/collector.yaml").read_text())
    for name, service in compose["services"].items():
        if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", service["image"]):
            raise SystemExit(name + ": image must have an immutable digest")
        for port in service.get("ports", []):
            if not str(port).startswith("127.0.0.1:"):
                raise SystemExit(name + ": published ports must bind to loopback")
        if service.get("security_opt") != ["no-new-privileges:true"]:
            raise SystemExit(name + ": no-new-privileges is required")
    for pipeline in collector["service"]["pipelines"].values():
        processors = pipeline["processors"]
        if processors.index("transform/allowlist") > processors.index("batch"):
            raise SystemExit("Capture allowlist must precede batching/export")
    native = yaml.safe_load((ROOT / "local/native/collector.yaml").read_text())
    jaeger = yaml.safe_load((ROOT / "local/native/jaeger.yaml").read_text())
    endpoints = [
        native["receivers"]["otlp"]["protocols"]["http"]["endpoint"],
        native["exporters"]["otlp_grpc/jaeger"]["endpoint"],
        native["exporters"]["prometheus"]["endpoint"],
        jaeger["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"],
        jaeger["extensions"]["jaeger_query"]["http"]["endpoint"],
        jaeger["extensions"]["jaeger_query"]["grpc"]["endpoint"],
    ]
    if any(not endpoint.startswith("127.0.0.1:") for endpoint in endpoints):
        raise SystemExit("Native endpoints must bind/connect to loopback")
    if native["processors"] != collector["processors"]:
        raise SystemExit("Native profile must preserve the reviewed capture processors")
    if set(jaeger["extensions"]) != {"jaeger_storage", "jaeger_query"}:
        raise SystemExit(
            "Native profile must not enable additional query/AI extensions"
        )
    print("Pinned images, loopback ports and capture pipeline ordering verified")


if __name__ == "__main__":
    main()
