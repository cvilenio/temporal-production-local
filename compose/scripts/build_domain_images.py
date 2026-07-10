#!/usr/bin/env python3
"""Descriptor-driven worker image build / push / digest emission (Phase 4).

Iterates config/domains/*.yaml workers[] and builds `<domain>-worker-<profile>` from
apps/temporal/workers/<language>/<domain>/<profile>/ using the resolved Dockerfile.

Per-language build-arg mapping lives ONLY in language_build_args() below.

Usage:
  build_domain_images.py build  [--registry REG] [--tag TAG] [--domain NAME]
  build_domain_images.py push   [--registry REG] [--tag TAG] [--domain NAME]
  build_domain_images.py digests [--registry REG] [--tag TAG] [--domain NAME]
  build_domain_images.py digests-json [--registry REG] [--tag TAG] --adopt NAME \
      [--kubeconfig PATH] [--k8s-namespace NS]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(
    os.environ.get("DOMAIN_VERIFY_ROOT", Path(__file__).resolve().parents[2])
).resolve()
DOMAINS_DIR = REPO_ROOT / "config" / "domains"

SUPPORTED_LANGUAGES = frozenset(
    {"python", "java", "go", "typescript", "ruby", "dotnet"}
)

# Maps worker language -> Dockerfile ARG name for optional runtime_version override.
_RUNTIME_VERSION_BUILD_ARG: dict[str, str] = {
    "python": "PYTHON_VERSION",
    "java": "JAVA_VERSION",
    "go": "GO_VERSION",
    "typescript": "NODE_VERSION",
    "ruby": "RUBY_VERSION",
}


@dataclass(frozen=True)
class WorkerSpec:
    domain: str
    profile: str
    language: str
    deployment_name: str
    k8s_namespace: str
    worker_dir: Path
    dockerfile: Path
    image_name: str
    digest_key: str
    build_args: dict[str, str]


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def worker_dir(domain: str, language: str, profile: str) -> Path:
    return REPO_ROOT / "apps/temporal/workers" / language / domain / profile


def resolve_dockerfile(worker: dict, language: str) -> Path:
    rel = str(worker.get("dockerfile") or f"images/{language}.Dockerfile")
    path = REPO_ROOT / rel
    if not path.is_file():
        raise FileNotFoundError(
            f"missing Dockerfile {rel} for language {language!r} - "
            f"add images/{language}.Dockerfile or set dockerfile: on the worker"
        )
    return path


def java_gradle_module(domain: str, profile: str) -> str:
    return f"{domain}-{profile}-worker"


def dotnet_runtime_parts(runtime_version: str | None) -> tuple[str, str]:
    """Map descriptor runtime_version to MSBuild TFM + SDK/runtime image tag."""
    rv = str(runtime_version or "net8.0").strip()
    if rv.startswith("net"):
        return rv, rv[3:]
    return f"net{rv}", rv


def dotnet_version_build_args(worker: dict) -> dict[str, str]:
    if not worker.get("runtime_version"):
        return {}
    tfm, tag = dotnet_runtime_parts(str(worker["runtime_version"]))
    return {"TARGET_FRAMEWORK": tfm, "DOTNET_VERSION": tag}


def runtime_version_build_args(language: str, worker: dict) -> dict[str, str]:
    """Emit the language's version build-arg when runtime_version is set on the worker."""
    rv = worker.get("runtime_version")
    if rv is None or rv == "":
        return {}
    lang = language.lower()
    arg_name = _RUNTIME_VERSION_BUILD_ARG.get(lang)
    if not arg_name:
        return {}
    return {arg_name: str(rv)}


def language_build_args(
    *,
    domain: str,
    profile: str,
    language: str,
    worker: dict,
    rel_worker_dir: str,
) -> dict[str, str]:
    lang = language.lower()
    args: dict[str, str]
    if lang == "python":
        dep_group = str(worker.get("dependency_group") or f"{domain}-workers")
        args = {
            "APP_GROUP": dep_group,
            "APP_PATH": rel_worker_dir,
            "APP_MODULE": "main",
            "APP_CMD": "python",
        }
    elif lang == "java":
        module = java_gradle_module(domain, profile)
        args = {
            "DOMAIN": domain,
            "APP_MODULE": f":{module}",
            "WORKER_REL_PATH": rel_worker_dir,
            "APP_JAR": module,
        }
    elif lang == "go":
        args = {
            "APP_PATH": rel_worker_dir,
            "APP_PKG": "./cmd",
        }
    elif lang == "typescript":
        args = {
            "APP_PATH": rel_worker_dir,
        }
    elif lang == "ruby":
        args = {
            "APP_PATH": rel_worker_dir,
        }
    elif lang == "dotnet":
        args = {
            "APP_PATH": rel_worker_dir,
            "DOMAIN": domain,
        }
        args.update(dotnet_version_build_args(worker))
    else:
        raise ValueError(f"unsupported worker language {language!r}")
    if lang != "dotnet":
        args.update(runtime_version_build_args(lang, worker))
    return args


def iter_workers(domain_filter: str | None = None) -> list[WorkerSpec]:
    if not DOMAINS_DIR.is_dir():
        return []
    specs: list[WorkerSpec] = []
    for desc_path in sorted(DOMAINS_DIR.glob("*.yaml")):
        descriptor = load_yaml(desc_path)
        domain = str(descriptor.get("domain") or desc_path.stem)
        if (
            domain_filter
            and domain != domain_filter
            and desc_path.stem != domain_filter
        ):
            continue
        k8s_namespace = str(descriptor.get("k8s_namespace") or domain)
        for worker in descriptor.get("workers") or []:
            profile = worker.get("profile")
            language = worker.get("language")
            if not profile or not language:
                raise ValueError(
                    f"{desc_path.relative_to(REPO_ROOT)}: each workers[] entry "
                    f"requires profile and language"
                )
            lang = str(language).lower()
            if lang not in SUPPORTED_LANGUAGES:
                raise ValueError(
                    f"{desc_path.relative_to(REPO_ROOT)}: workers[{profile!r}] "
                    f"language {language!r} unsupported"
                )
            wdir = worker_dir(domain, lang, str(profile))
            if not wdir.is_dir():
                raise FileNotFoundError(
                    f"missing worker dir {wdir.relative_to(REPO_ROOT)}/ for "
                    f"{domain}/{profile} ({lang}) - scaffold or move code before building"
                )
            dockerfile = resolve_dockerfile(worker, lang)
            rel_dir = wdir.relative_to(REPO_ROOT).as_posix()
            specs.append(
                WorkerSpec(
                    domain=domain,
                    profile=str(profile),
                    language=lang,
                    deployment_name=str(worker["deployment_name"]),
                    k8s_namespace=k8s_namespace,
                    worker_dir=wdir,
                    dockerfile=dockerfile,
                    image_name=f"{domain}-worker-{profile}",
                    digest_key=f"{domain}-{profile}",
                    build_args=language_build_args(
                        domain=domain,
                        profile=str(profile),
                        language=lang,
                        worker=worker,
                        rel_worker_dir=rel_dir,
                    ),
                )
            )
    return specs


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(cmd)}", flush=True, file=sys.stderr)
    return subprocess.run(
        cmd, cwd=REPO_ROOT, check=True, capture_output=True, text=True
    )


def docker_build(spec: WorkerSpec, registry: str, tag: str) -> None:
    image = f"{registry}/{spec.image_name}:{tag}"
    cmd = [
        "docker",
        "build",
        "-f",
        str(spec.dockerfile.relative_to(REPO_ROOT)),
    ]
    for key, value in spec.build_args.items():
        cmd.extend(["--build-arg", f"{key}={value}"])
    cmd.extend(["-t", image, "."])
    run(cmd)
    print(f"Built {image} from {spec.worker_dir.relative_to(REPO_ROOT)}/")


def docker_push(spec: WorkerSpec, registry: str, tag: str) -> None:
    image = f"{registry}/{spec.image_name}:{tag}"
    run(["docker", "push", image])


def docker_digest(spec: WorkerSpec, registry: str, tag: str) -> str:
    image = f"{registry}/{spec.image_name}:{tag}"
    result = run(["crane", "digest", image, "--insecure"])
    return result.stdout.strip()


def parse_image_digest(image: str) -> str:
    if "@sha256:" not in image:
        raise ValueError(f"image ref missing digest: {image!r}")
    return "sha256:" + image.split("@sha256:", 1)[1]


def live_worker_digest(
    spec: WorkerSpec, *, kubeconfig: Path, k8s_namespace: str | None = None
) -> str | None:
    ns = k8s_namespace or spec.k8s_namespace
    result = subprocess.run(
        [
            "kubectl",
            f"--kubeconfig={kubeconfig}",
            "-n",
            ns,
            "get",
            "pods",
            "-l",
            f"app.kubernetes.io/name={spec.deployment_name}",
            "-o",
            "jsonpath={.items[0].spec.containers[0].image}",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    image = result.stdout.strip()
    if result.returncode != 0 or not image:
        return None
    return parse_image_digest(image)


def digests_json(
    specs: list[WorkerSpec],
    *,
    registry: str,
    tag: str,
    adopt_domain: str | None,
    kubeconfig: Path | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in specs:
        if adopt_domain and spec.domain == adopt_domain:
            out[spec.digest_key] = docker_digest(spec, registry, tag)
        elif adopt_domain and kubeconfig is not None:
            live = live_worker_digest(spec, kubeconfig=kubeconfig)
            if live is not None:
                out[spec.digest_key] = live
            else:
                out[spec.digest_key] = docker_digest(spec, registry, tag)
        else:
            out[spec.digest_key] = docker_digest(spec, registry, tag)
    return out


def git_describe_tag() -> str:
    return subprocess.check_output(
        ["git", "describe", "--tags", "--always", "--dirty", "--abbrev=12"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Descriptor-driven domain worker images"
    )
    parser.add_argument(
        "action",
        choices=("build", "push", "digests", "digests-json"),
        help="build, push, print digests, or emit JSON digest map",
    )
    parser.add_argument(
        "--registry", default=os.environ.get("REGISTRY", "localhost:5001")
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="image tag (default: git describe)",
    )
    parser.add_argument(
        "--domain",
        default=None,
        help="limit to one domain key (build/push/digests for adopted domain only)",
    )
    parser.add_argument(
        "--adopt",
        default=None,
        help="with digests-json: fresh registry digests for this domain, live digests for others",
    )
    parser.add_argument(
        "--kubeconfig",
        default=os.environ.get("KUBECONFIG", ".secrets/kube/kind.kubeconfig"),
        help="kubeconfig for live digest lookup (digests-json with --adopt)",
    )
    args = parser.parse_args()
    tag = args.tag or git_describe_tag()

    specs = iter_workers(args.domain)
    if not specs:
        print(f"OK: no domain descriptors under {DOMAINS_DIR.relative_to(REPO_ROOT)}/")
        return

    if args.action == "build":
        for spec in specs:
            docker_build(spec, args.registry, tag)
        names = ", ".join(s.image_name for s in specs)
        print(f"Built {len(specs)} worker image(s): {names}")
    elif args.action == "push":
        for spec in specs:
            docker_push(spec, args.registry, tag)
        names = ", ".join(s.image_name for s in specs)
        print(f"Pushed {len(specs)} worker image(s): {names}")
    elif args.action == "digests":
        for spec in specs:
            digest = docker_digest(spec, args.registry, tag)
            print(f"{spec.digest_key}={digest}")
    else:
        all_specs = iter_workers(None)
        if not all_specs:
            print("{}")
            return
        adopt = args.adopt or args.domain
        kube = Path(args.kubeconfig).expanduser()
        if adopt and not kube.is_file():
            raise FileNotFoundError(f"kubeconfig not found: {kube}")
        mapping = digests_json(
            all_specs,
            registry=args.registry,
            tag=tag,
            adopt_domain=adopt,
            kubeconfig=kube if adopt else None,
        )
        print(json.dumps(mapping, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
