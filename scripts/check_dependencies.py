"""Verify locked chart bytes and offline renderer ownership; never install a chart."""

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = Path(
    os.environ.get("CHART_INPUT_CACHE", ROOT.parent / ".runtime/chart-inputs")
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private(path):
    path = Path(path).resolve()
    for parent in (path, *path.parents):
        if (parent / ".git").exists() or (
            (parent / "HEAD").is_file() and (parent / "objects").is_dir()
        ):
            raise ValueError("Renderer cache must remain outside Git")
    return path


def downloaded(url, expected, target, restore):
    target = private(target)
    if not target.exists():
        if not restore:
            raise ValueError("Locked cache missing; explicit --restore required")
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(100 * 1024 * 1024 + 1)
        if len(data) > 100 * 1024 * 1024 or sha(data) != expected:
            raise ValueError("Downloaded input checksum/size mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    data = target.read_bytes()
    if sha(data) != expected:
        raise ValueError("Cached input checksum mismatch")
    return data


def check(cache=DEFAULT_CACHE, restore=False):
    cache = private(cache)
    lock = json.loads((ROOT / "locks/dependencies.json").read_text())
    dependency = lock["dependencies"][0]
    if (
        lock.get("schema_version") != 1
        or len(lock["dependencies"]) != 1
        or not re.fullmatch(r"\d+\.\d+\.\d+", dependency.get("version", ""))
    ):
        raise ValueError("One exact dependency version is required")
    chart = ROOT / lock["wrapper"]
    if chart.resolve() != ROOT / "charts/argocd" or lock.get("deployable") is not False:
        raise ValueError("Only the disabled dependency scaffold is accepted")
    source = yaml.safe_load((chart / "Chart.yaml").read_text())
    generated = yaml.safe_load((chart / "Chart.lock").read_text())
    expected = {
        "name": "argo-cd",
        "version": dependency["version"],
        "repository": dependency["repository"],
    }
    if (
        source["appVersion"] != dependency["app_version"]
        or generated["dependencies"] != [expected]
        or any(
            source["dependencies"][0].get(key) != value
            for key, value in expected.items()
        )
    ):
        raise ValueError("Chart declaration/generated lock/version mismatch")
    values = yaml.safe_load((chart / "values.yaml").read_text())
    if (
        values["argo-cd"]["enabled"] is not False
        or values["argo-cd"]["crds"]["install"] is not False
    ):
        raise ValueError("Default activation or duplicate CRD ownership forbidden")
    version = dependency["version"]
    archive_url = f"https://github.com/argoproj/argo-helm/releases/download/argo-cd-{version}/argo-cd-{version}.tgz"
    if dependency["archive_url"] != archive_url:
        raise ValueError("Unreviewed chart release URL")
    archive = downloaded(
        archive_url,
        dependency["archive_sha256"],
        cache / f"argo-cd-{version}.tgz",
        restore,
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as package:
        metadata = yaml.safe_load(package.extractfile("argo-cd/Chart.yaml").read())
        transitives = {
            member.name: sha(package.extractfile(member).read())
            for member in package
            if member.isfile() and member.name.endswith(".tgz")
        }
    if (
        metadata["version"] != version
        or metadata["appVersion"].removeprefix("v") != dependency["app_version"]
        or transitives != dependency["transitive_archive_sha256"]
    ):
        raise ValueError("Upstream/transitive archive mismatch")
    tool = json.loads((ROOT / "locks/toolchain.json").read_text())["tools"]["helm"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", tool.get("version", "")):
        raise ValueError("Exact renderer version required")
    system = platform.system().lower()
    if system not in {"windows", "linux"} or platform.machine().lower() not in {
        "amd64",
        "x86_64",
    }:
        raise ValueError("Unsupported renderer platform")
    row = tool["platforms"][system + "_amd64"]
    suffix = "zip" if system == "windows" else "tar.gz"
    filename = f"helm-v{tool['version']}-{system}-amd64.{suffix}"
    member = system + "-amd64/helm" + (".exe" if system == "windows" else "")
    if row["url"] != "https://get.helm.sh/" + filename or row["member"] != member:
        raise ValueError("Unreviewed Helm download/member")
    data = downloaded(row["url"], row["archive_sha256"], cache / filename, restore)
    if system == "windows":
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            binary = package.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as package:
            entry = package.getmember(member)
            if not entry.isfile():
                raise ValueError("Renderer member is not a file")
            binary = package.extractfile(entry).read()
    if sha(binary) != row["binary_sha256"]:
        raise ValueError("Renderer binary checksum mismatch")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        stage = Path(directory)
        executable = stage / ("helm.exe" if system == "windows" else "helm")
        executable.write_bytes(binary)
        executable.chmod(0o700)
        copied = stage / "chart"
        copied.mkdir()
        for name in ["Chart.yaml", "Chart.lock", "values.yaml", ".helmignore"]:
            shutil.copyfile(chart / name, copied / name)
        (copied / "charts").mkdir()
        (copied / "charts" / f"argo-cd-{version}.tgz").write_bytes(archive)
        env = {
            **os.environ,
            "HELM_CONFIG_HOME": str(stage / "config"),
            "HELM_CACHE_HOME": str(stage / "cache"),
            "HELM_DATA_HOME": str(stage / "data"),
        }
        counts = {}
        for mode, extra in [
            ("disabled", []),
            ("smoke", ["--set", "argo-cd.enabled=true"]),
        ]:
            result = subprocess.run(
                [
                    str(executable),
                    "template",
                    "lock-check",
                    str(copied),
                    "--kube-version",
                    "1.35.0",
                    "--skip-crds",
                    *extra,
                ],
                env=env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
            )
            if result.returncode:
                raise ValueError("Offline locked Helm render failed")
            objects = [obj for obj in yaml.safe_load_all(result.stdout) if obj]
            if any(
                obj["kind"] in {"Namespace", "CustomResourceDefinition"}
                for obj in objects
            ):
                raise ValueError("Controller chart emitted config-owned namespace/CRD")
            counts[mode] = len(objects)
        if counts["disabled"] or not counts["smoke"]:
            raise ValueError(
                "Disabled/smoke ownership check did not execute as expected"
            )
    return {
        "status": "passed_dependency_integrity_and_offline_smoke",
        "chart_version": version,
        "argo_cd_version": dependency["app_version"],
        "helm_version": tool["version"],
        "upstream_archive_sha256": dependency["archive_sha256"],
        "transitive_archives": len(transitives),
        "default_objects": counts["disabled"],
        "smoke_objects": counts["smoke"],
        "namespace_or_crd_emission": False,
        "deployable": False,
        "cluster_operations": False,
        "limitations": [
            "Explicitly enabled smoke rendering verifies dependency/ownership coherence only; controller image digests, hardening, connectivity and bootstrap adoption remain unproved."
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Download checksum-locked public inputs only; never install a chart",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check(args.cache, args.restore)
    if args.output:
        args.output.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
