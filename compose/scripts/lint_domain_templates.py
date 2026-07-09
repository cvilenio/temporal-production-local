#!/usr/bin/env python3
"""Compile-check Go and typecheck TypeScript domain templates offline."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_REPO = Path(__file__).resolve().parents[2]
GO_DOMAIN = "golint"
TS_DOMAIN = "tlint"


def substitute(text: str, domain: str) -> str:
    return (
        text.replace("{{DOMAIN}}", domain)
        .replace("{{DOMAIN_PKG}}", domain.replace("-", "_"))
        .replace("{{LANG}}", "go")
    )


def copy_tree(src: Path, dst: Path, domain: str) -> None:
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        rel_parts = [substitute(part, domain) for part in rel.parts]
        out = dst.joinpath(*rel_parts)
        if path.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        data = (
            path.read_text()
            if path.suffix in {".go", ".mod", ".ts", ".json"}
            else path.read_bytes()
        )
        if isinstance(data, str):
            out.write_text(substitute(data, domain))
        else:
            out.write_bytes(data)


def lint_go_templates() -> None:
    src = SCRIPT_REPO / "templates/domain/go/apps/temporal/workers/go/{{DOMAIN}}"
    if not src.is_dir():
        return
    with tempfile.TemporaryDirectory(prefix="go-template-lint-") as tmp:
        root = Path(tmp)
        for profile in ("workflow", "activity"):
            profile_src = src / profile
            if not profile_src.is_dir():
                continue
            dst = root / "apps/temporal/workers/go" / GO_DOMAIN / profile
            copy_tree(profile_src, dst, GO_DOMAIN)
            subprocess.run(["go", "mod", "tidy"], cwd=dst, check=True)
            subprocess.run(
                ["go", "build", "-o", "/dev/null", "./cmd"], cwd=dst, check=True
            )
    print("Go domain templates compile ok.")


def lint_typescript_templates() -> None:
    src_root = SCRIPT_REPO / "templates/domain/typescript"
    if not src_root.is_dir():
        return
    with tempfile.TemporaryDirectory(prefix="ts-template-lint-") as tmp:
        root = Path(tmp)
        lib_src = src_root / "libs/{{DOMAIN}}/typescript"
        lib_dst = root / "libs" / TS_DOMAIN / "typescript"
        copy_tree(lib_src, lib_dst, TS_DOMAIN)
        for profile in ("workflow", "activity"):
            profile_src = (
                src_root / f"apps/temporal/workers/typescript/{{{{DOMAIN}}}}/{profile}"
            )
            if not profile_src.is_dir():
                continue
            dst = root / "apps/temporal/workers/typescript" / TS_DOMAIN / profile
            copy_tree(profile_src, dst, TS_DOMAIN)
        workspace = root / "pnpm-workspace.yaml"
        workspace.write_text(
            f'packages:\n  - "libs/{TS_DOMAIN}/typescript"\n'
            f'  - "apps/temporal/workers/typescript/{TS_DOMAIN}/*"\n'
        )
        pkg = {
            "name": "template-lint-root",
            "private": True,
            "type": "module",
        }
        (root / "package.json").write_text(
            __import__("json").dumps(pkg, indent=2) + "\n"
        )
        for profile in ("workflow", "activity"):
            pkg_path = root / "apps/temporal/workers/typescript" / TS_DOMAIN / profile
            if not pkg_path.is_dir():
                continue
            tsconfig = {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "NodeNext",
                    "moduleResolution": "NodeNext",
                    "strict": True,
                    "skipLibCheck": True,
                    "outDir": "dist",
                    "rootDir": "src",
                },
                "include": ["src/**/*.ts"],
            }
            (pkg_path / "tsconfig.json").write_text(
                __import__("json").dumps(tsconfig, indent=2) + "\n"
            )
        lib_path = root / "libs" / TS_DOMAIN / "typescript"
        (lib_path / "tsconfig.json").write_text(
            __import__("json").dumps(
                {
                    "compilerOptions": {
                        "target": "ES2022",
                        "module": "NodeNext",
                        "moduleResolution": "NodeNext",
                        "strict": True,
                        "skipLibCheck": True,
                        "declaration": True,
                        "outDir": "dist",
                        "rootDir": "src",
                    },
                    "include": ["src/**/*.ts"],
                },
                indent=2,
            )
            + "\n"
        )
        subprocess.run(
            ["npx", "-y", "pnpm@9.15.0", "install", "--no-frozen-lockfile"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            [
                "npx",
                "-y",
                "pnpm@9.15.0",
                "exec",
                "tsc",
                "-p",
                "tsconfig.json",
            ],
            cwd=lib_path,
            check=True,
        )
        for profile in ("workflow", "activity"):
            pkg_path = root / "apps/temporal/workers/typescript" / TS_DOMAIN / profile
            if (pkg_path / "src").is_dir():
                subprocess.run(
                    [
                        "npx",
                        "-y",
                        "pnpm@9.15.0",
                        "exec",
                        "tsc",
                        "--noEmit",
                        "-p",
                        "tsconfig.json",
                    ],
                    cwd=pkg_path,
                    check=True,
                )
    print("TypeScript domain templates typecheck ok.")


def main() -> None:
    lint_go_templates()
    lint_typescript_templates()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"FAIL: domain template lint failed: {exc}", file=sys.stderr)
        sys.exit(1)
