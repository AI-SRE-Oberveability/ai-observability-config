"""Offline ephemeral Jaeger chart checks; never install or contact a cluster."""

import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import yaml
from check_collector_chart import renderer
from check_dependencies import DEFAULT_CACHE, private

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/jaeger"


def inspect(objects):
    if len(objects) != 4 or {(o["apiVersion"], o["kind"]) for o in objects} != {
        ("v1", "ServiceAccount"),
        ("v1", "ConfigMap"),
        ("v1", "Service"),
        ("apps/v1", "Deployment"),
    }:
        raise ValueError("Exactly four owned objects required")
    if any(
        o["metadata"]["name"] != "jaeger"
        or o["metadata"]["namespace"] != "ai-observability"
        for o in objects
    ):
        raise ValueError("Unapproved identity")
    kinds = {o["kind"]: o for o in objects}
    labels = {"app.kubernetes.io/name": "jaeger"}
    if kinds["ServiceAccount"].get("automountServiceAccountToken") is not False:
        raise ValueError("Tokenless service account required")
    if kinds["Service"]["spec"] != {
        "type": "ClusterIP",
        "selector": labels,
        "ports": [{"name": "otlp-grpc", "port": 4317, "targetPort": 4317}],
    }:
        raise ValueError("Only internal OTLP service permitted")
    deploy = kinds["Deployment"]["spec"]
    pod = deploy["template"]["spec"]
    if (
        deploy["replicas"] != 1
        or deploy["strategy"] != {"type": "Recreate"}
        or deploy["selector"]["matchLabels"] != labels
        or deploy["template"]["metadata"]["labels"]["app.kubernetes.io/name"]
        != "jaeger"
    ):
        raise ValueError("Single matching ephemeral instance required")
    if (
        pod.get("automountServiceAccountToken") is not False
        or pod["serviceAccountName"] != "jaeger"
        or any(
            pod.get(k)
            for k in [
                "hostNetwork",
                "hostPID",
                "hostIPC",
                "initContainers",
                "ephemeralContainers",
            ]
        )
    ):
        raise ValueError("Pod privilege/sidecar forbidden")
    if (
        pod["securityContext"]
        != {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        }
        or len(pod["containers"]) != 1
    ):
        raise ValueError("Restricted pod required")
    container = pod["containers"][0]
    image = yaml.safe_load((ROOT / "local/compose.yaml").read_text(encoding="utf-8"))[
        "services"
    ]["jaeger"]["image"]
    if (
        container["image"] != image
        or "@sha256:" not in image
        or container.get("env")
        or container.get("envFrom")
        or container.get("command")
    ):
        raise ValueError("Reviewed pinned image/config execution required")
    if container["securityContext"] != {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }:
        raise ValueError("Restricted container required")
    if container["resources"] != {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "500m", "memory": "256Mi"},
    } or container["ports"] != [{"name": "otlp-grpc", "containerPort": 4317}]:
        raise ValueError("Bounded resources/internal ports required")
    if any(
        container[k].get("tcpSocket") != {"port": "otlp-grpc"}
        or "httpGet" in container[k]
        for k in ["startupProbe", "readinessProbe", "livenessProbe"]
    ):
        raise ValueError("Ingestion socket probes required")
    if (
        container["args"] != ["--config=/etc/otelcol/config.yaml"]
        or container["volumeMounts"]
        != [{"name": "config", "mountPath": "/etc/otelcol", "readOnly": True}]
        or pod["volumes"] != [{"name": "config", "configMap": {"name": "jaeger"}}]
    ):
        raise ValueError("Read-only config only; no persistent/host volume")
    text = kinds["ConfigMap"]["data"]["config.yaml"]
    config = yaml.safe_load(text)
    expected = yaml.safe_load(
        (ROOT / "local/native/jaeger.yaml").read_text(encoding="utf-8")
    )
    expected["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"] = "0.0.0.0:4317"
    expected["processors"]["memory_limiter"] = {
        "check_interval": "1s",
        "limit_mib": 128,
        "spike_limit_mib": 32,
    }
    expected["processors"]["batch"]["send_batch_max_size"] = 256
    expected["service"]["pipelines"]["traces"]["processors"] = [
        "memory_limiter",
        "batch",
    ]
    if config != expected:
        raise ValueError("Ephemeral/loopback query/config bounds changed")
    if (
        deploy["template"]["metadata"]["annotations"]["checksum/config"]
        != hashlib.sha256((CHART / "files/config.yaml").read_bytes()).hexdigest()
    ):
        raise ValueError("Config checksum mismatch")
    return text


def validate(cache=DEFAULT_CACHE, restore=False):
    cache = private(cache)
    binary = renderer(cache, restore)
    env = {
        **os.environ,
        "HELM_CONFIG_HOME": str(cache / "jaeger-config"),
        "HELM_CACHE_HOME": str(cache / "jaeger-cache"),
        "HELM_DATA_HOME": str(cache / "jaeger-data"),
    }

    def helm(*args):
        return subprocess.run(
            [str(binary), *args],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    if helm("lint", str(CHART), "--strict", "--set", "enabled=true").returncode:
        raise ValueError("Jaeger Helm lint failed")
    reports = {}
    enabled = None
    text = None
    for mode, extra in [
        ("disabled", []),
        ("dev", ["--set", "enabled=true,profile=dev"]),
        ("qa", ["--set", "enabled=true,profile=qa"]),
    ]:
        result = helm(
            "template",
            "jaeger",
            str(CHART),
            "--namespace",
            "ai-observability",
            "--kube-version",
            "1.35.0",
            *extra,
        )
        if result.returncode:
            raise ValueError("Jaeger render failed")
        objects = [o for o in yaml.safe_load_all(result.stdout) if o]
        if mode == "disabled":
            if objects:
                raise ValueError("Default must emit zero objects")
        else:
            text = inspect(objects)
            enabled = objects
        reports[mode] = {
            "objects": len(objects),
            "sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        }
    rejected = []
    for name, extra in [
        ("tag_only_image", ["--set-string", "image=jaeger:latest"]),
        ("unknown_public_ui", ["--set", "publicUI=true"]),
        ("unapproved_profile", ["--set", "profile=production"]),
    ]:
        if (
            helm(
                "template", "negative", str(CHART), "--set", "enabled=true", *extra
            ).returncode
            == 0
        ):
            raise ValueError("Unsafe values accepted")
        rejected.append(name)
    for name in [
        "public_service",
        "ui_port",
        "token_mount",
        "privilege",
        "persistent_storage",
        "query_exposure",
        "namespace_escape",
        "duplicate_owner",
    ]:
        objects = copy.deepcopy(enabled)
        kinds = {o["kind"]: o for o in objects}
        if name == "public_service":
            kinds["Service"]["spec"]["type"] = "LoadBalancer"
        elif name == "ui_port":
            kinds["Service"]["spec"]["ports"].append({"port": 16686})
        elif name == "token_mount":
            kinds["Deployment"]["spec"]["template"]["spec"][
                "automountServiceAccountToken"
            ] = True
        elif name == "privilege":
            kinds["Deployment"]["spec"]["template"]["spec"]["containers"][0][
                "securityContext"
            ]["allowPrivilegeEscalation"] = True
        elif name in {"persistent_storage", "query_exposure"}:
            config = yaml.safe_load(kinds["ConfigMap"]["data"]["config.yaml"])
            if name == "query_exposure":
                config["extensions"]["jaeger_query"]["http"]["endpoint"] = (
                    "0.0.0.0:16686"
                )
            else:
                config["extensions"]["jaeger_storage"]["backends"]["local_memory"] = {
                    "badger": {"ephemeral": False}
                }
            kinds["ConfigMap"]["data"]["config.yaml"] = yaml.safe_dump(config)
        elif name == "namespace_escape":
            objects[0]["metadata"]["namespace"] = "argocd"
        else:
            objects.append(copy.deepcopy(objects[0]))
        try:
            inspect(objects)
        except ValueError:
            rejected.append(name)
        else:
            raise ValueError("Unsafe rendered mutation accepted: " + name)
    return {
        "status": "passed_offline_jaeger_chart_contract",
        "profiles": reports,
        "negative_cases_rejected": rejected,
        "ephemeral_max_traces": 1000,
        "api_schema_baseline": "1.35.0_offline_only",
        "deployable": False,
        "cluster_operations": False,
    }, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config-out", type=Path)
    args = parser.parse_args()
    report, text = validate(args.cache, args.restore)
    if args.output:
        private(args.output).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    if args.config_out:
        private(args.config_out).write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
