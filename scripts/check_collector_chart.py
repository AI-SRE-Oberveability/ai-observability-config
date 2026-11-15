"""Offline Collector chart contract checks; never install or contact a cluster."""

import argparse
import copy
import hashlib
import io
import json
import os
import platform
import subprocess
import tarfile
import zipfile
from pathlib import Path

import yaml
from check_dependencies import DEFAULT_CACHE, downloaded, private

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/otel-collector"


def renderer(cache, restore=False):
    tool = json.loads((ROOT / "locks/toolchain.json").read_text())["tools"]["helm"]
    system = platform.system().lower()
    if system not in {"windows", "linux"} or platform.machine().lower() not in {
        "amd64",
        "x86_64",
    }:
        raise ValueError("Unsupported renderer platform")
    row = tool["platforms"][system + "_amd64"]
    suffix = "zip" if system == "windows" else "tar.gz"
    filename = f"helm-v{tool['version']}-{system}-amd64.{suffix}"
    if row["url"] != "https://get.helm.sh/" + filename:
        raise ValueError("Unreviewed renderer URL")
    data = downloaded(row["url"], row["archive_sha256"], cache / filename, restore)
    if system == "windows":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            binary = archive.read(row["member"])
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            binary = archive.extractfile(row["member"]).read()
    if hashlib.sha256(binary).hexdigest() != row["binary_sha256"]:
        raise ValueError("Renderer binary integrity mismatch")
    path = private(
        cache / ("helm-collector.exe" if system == "windows" else "helm-collector")
    )
    path.write_bytes(binary)
    path.chmod(0o700)
    return path


def inspect(objects, namespace="ai-observability"):
    expected = {
        ("v1", "ServiceAccount"),
        ("v1", "ConfigMap"),
        ("v1", "Service"),
        ("apps/v1", "Deployment"),
    }
    if len(objects) != 4 or {(o["apiVersion"], o["kind"]) for o in objects} != expected:
        raise ValueError("Exactly four chart-owned kinds required")
    if any(
        o["metadata"]["namespace"] != namespace
        or o["metadata"]["name"] != "otel-collector"
        for o in objects
    ):
        raise ValueError("Unapproved namespace/name")
    by_kind = {o["kind"]: o for o in objects}
    if by_kind["ServiceAccount"].get("automountServiceAccountToken") is not False:
        raise ValueError("Service account must be tokenless")
    service = by_kind["Service"]["spec"]
    labels = {"app.kubernetes.io/name": "otel-collector"}
    if (
        service.get("type") != "ClusterIP"
        or service.get("externalIPs")
        or service.get("loadBalancerIP")
        or service["selector"] != labels
    ):
        raise ValueError("Internal service/selector required")
    if service["ports"] != [
        {"name": "otlp-grpc", "port": 4317, "targetPort": 4317},
        {"name": "otlp-http", "port": 4318, "targetPort": 4318},
    ]:
        raise ValueError("Only OTLP service ports allowed")
    deploy = by_kind["Deployment"]["spec"]
    pod = deploy["template"]["spec"]
    if (
        deploy["replicas"] != 1
        or deploy["selector"]["matchLabels"] != labels
        or deploy["template"]["metadata"]["labels"]["app.kubernetes.io/name"]
        != "otel-collector"
    ):
        raise ValueError("Single matching Collector instance required")
    if (
        pod.get("automountServiceAccountToken") is not False
        or pod.get("serviceAccountName") != "otel-collector"
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
        raise ValueError("Unexpected pod privilege or sidecar")
    if pod["securityContext"] != {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }:
        raise ValueError("Restricted pod identity required")
    if len(pod["containers"]) != 1:
        raise ValueError("One container required")
    container = pod["containers"][0]
    image = yaml.safe_load((ROOT / "local/compose.yaml").read_text())["services"][
        "collector"
    ]["image"]
    if (
        container["image"] != image
        or "@sha256:" not in image
        or container.get("env")
        or container.get("envFrom")
        or container.get("command")
    ):
        raise ValueError("Reviewed digest/config-only execution required")
    if container["securityContext"] != {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }:
        raise ValueError("Restricted container context required")
    if container["resources"] != {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "500m", "memory": "256Mi"},
    }:
        raise ValueError("Reviewed resource bounds required")
    if any(
        container[k]["httpGet"] != {"path": "/", "port": "health"}
        for k in ["startupProbe", "readinessProbe", "livenessProbe"]
    ):
        raise ValueError("Process health probes required")
    if any(p.get("hostPort") for p in container["ports"]):
        raise ValueError("Host ports forbidden")
    if (
        container["args"] != ["--config=/etc/otelcol/config.yaml"]
        or container["volumeMounts"]
        != [{"name": "config", "mountPath": "/etc/otelcol", "readOnly": True}]
        or pod["volumes"]
        != [{"name": "config", "configMap": {"name": "otel-collector"}}]
    ):
        raise ValueError("Read-only config-only volume required")
    text = by_kind["ConfigMap"]["data"]["config.yaml"]
    config = yaml.safe_load(text)
    baseline = yaml.safe_load((ROOT / "local/collector.yaml").read_text())
    baseline["extensions"] = {"health_check": {"endpoint": "0.0.0.0:13133"}}
    baseline["service"]["extensions"] = ["health_check"]
    if config != baseline:
        raise ValueError("Capture/export bounds differ from reviewed profile")
    checksum = hashlib.sha256((CHART / "files/config.yaml").read_bytes()).hexdigest()
    if deploy["template"]["metadata"]["annotations"]["checksum/config"] != checksum:
        raise ValueError("Config rollout checksum mismatch")
    return text


def validate(cache=DEFAULT_CACHE, restore=False):
    cache = private(cache)
    executable = renderer(cache, restore)
    env = {
        **os.environ,
        "HELM_CONFIG_HOME": str(cache / "collector-config"),
        "HELM_CACHE_HOME": str(cache / "collector-cache"),
        "HELM_DATA_HOME": str(cache / "collector-data"),
    }

    def helm(*args):
        return subprocess.run(
            [str(executable), *args],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )

    lint = helm("lint", str(CHART), "--strict", "--set", "enabled=true")
    if lint.returncode:
        raise ValueError("Helm lint failed: " + lint.stderr[-300:])
    reports = {}
    config = None
    for mode, extra in [
        ("disabled", []),
        ("dev", ["--set", "enabled=true,profile=dev"]),
        ("qa", ["--set", "enabled=true,profile=qa"]),
    ]:
        result = helm(
            "template",
            "otel-collector",
            str(CHART),
            "--namespace",
            "ai-observability",
            "--kube-version",
            "1.35.0",
            *extra,
        )
        if result.returncode:
            raise ValueError("Helm template failed")
        objects = [o for o in yaml.safe_load_all(result.stdout) if o]
        if mode == "disabled":
            if objects:
                raise ValueError("Default must emit zero objects")
        else:
            config = inspect(objects)
        reports[mode] = {
            "objects": len(objects),
            "sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        }
    failures = []
    for name, extra in [
        ("tag_only_image", ["--set-string", "image=otel/collector:latest"]),
        ("unknown_value", ["--set", "publicService=true"]),
        ("invalid_profile", ["--set", "profile=production"]),
    ]:
        if (
            helm(
                "template", "negative", str(CHART), "--set", "enabled=true", *extra
            ).returncode
            == 0
        ):
            raise ValueError("Negative values case was accepted: " + name)
        failures.append(name)
    objects = [
        o
        for o in yaml.safe_load_all(
            helm(
                "template",
                "otel-collector",
                str(CHART),
                "--namespace",
                "ai-observability",
                "--set",
                "enabled=true",
            ).stdout
        )
        if o
    ]
    for name in [
        "public_service",
        "token_mount",
        "privilege",
        "capture_change",
        "namespace_escape",
        "duplicate_owner",
    ]:
        changed = copy.deepcopy(objects)
        by_kind = {o["kind"]: o for o in changed}
        if name == "public_service":
            by_kind["Service"]["spec"]["type"] = "LoadBalancer"
        elif name == "token_mount":
            by_kind["Deployment"]["spec"]["template"]["spec"][
                "automountServiceAccountToken"
            ] = True
        elif name == "privilege":
            by_kind["Deployment"]["spec"]["template"]["spec"]["containers"][0][
                "securityContext"
            ]["allowPrivilegeEscalation"] = True
        elif name == "capture_change":
            by_kind["ConfigMap"]["data"]["config.yaml"] = "receivers: {}"
        elif name == "namespace_escape":
            changed[0]["metadata"]["namespace"] = "argocd"
        else:
            changed.append(copy.deepcopy(changed[0]))
        try:
            inspect(changed)
        except ValueError:
            failures.append(name)
        else:
            raise ValueError("Negative render case was accepted: " + name)
    return {
        "status": "passed_offline_collector_chart_contract",
        "profiles": reports,
        "negative_cases_rejected": failures,
        "api_schema_baseline": "1.35.0_offline_only",
        "deployable": False,
        "cluster_operations": False,
    }, config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config-out", type=Path)
    args = parser.parse_args()
    report, config = validate(args.cache, args.restore)
    if args.output:
        private(args.output).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    if args.config_out:
        private(args.config_out).write_text(config, encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
