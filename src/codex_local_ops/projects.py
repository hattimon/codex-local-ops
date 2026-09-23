from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from .safety import assert_trusted_path
from .processes import run


MARKERS = {
    "python": ["pyproject.toml", "requirements.txt", "setup.py"],
    "django": ["manage.py"],
    "docker": ["Dockerfile"],
    "docker-compose": ["compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
    "php": ["composer.json"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "dotnet": ["*.csproj", "*.sln"],
    "powershell": ["*.ps1", "*.psm1"],
    "bash": ["*.sh"],
    "c-cpp": ["CMakeLists.txt", "Makefile", "*.c", "*.cpp", "*.h", "*.hpp"],
}


def detect(path: str) -> dict:
    root = assert_trusted_path(path, must_exist=True)
    if root.is_file():
        root = root.parent
    found: set[str] = set()
    manifests: list[str] = []
    for kind, patterns in MARKERS.items():
        for pattern in patterns:
            matches = list(root.glob(pattern))
            if matches:
                found.add(kind)
                manifests.extend(str(p.name) for p in matches[:5])
    package = root / "package.json"
    if package.exists():
        found.add("node")
        manifests.append("package.json")
        try:
            pkg = json.loads(package.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for dep, kind in {"react": "react", "vue": "vue", "svelte": "svelte", "next": "nextjs", "vite": "vite"}.items():
                if dep in deps:
                    found.add(kind)
        except Exception:
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
        for needle, kind in [("fastapi", "fastapi"), ("django", "django"), ("flask", "flask")]:
            if needle in text:
                found.add(kind)
    cfg = root / ".codex-local-ops" / "project.yaml"
    return {"root": str(root), "types": sorted(found), "manifests": sorted(set(manifests)), "project_config": str(cfg) if cfg.exists() else None, "git": (root / ".git").exists()}


def load_project_config(path: Path) -> dict:
    cfg = path / ".codex-local-ops" / "project.yaml"
    if not cfg.exists():
        return {}
    data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("project.yaml must contain a mapping")
    return data


def create(path: str, template: str = "empty") -> dict:
    root = assert_trusted_path(path)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Project directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    template = template.lower()
    files: dict[str, str] = {"README.md": f"# {root.name}\n"}
    if template in {"python", "fastapi", "django"}:
        files["pyproject.toml"] = f'[project]\nname = "{root.name.lower().replace(" ", "-")}"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
        files["src/__init__.py"] = ""
    if template == "fastapi":
        files["src/main.py"] = "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/health')\ndef health():\n    return {'status': 'ok'}\n"
    if template == "django":
        files["README.md"] += "\nInitialize Django with: python -m django startproject app .\n"
    if template in {"node", "react", "vite", "nextjs"}:
        pkg = {"name": root.name.lower().replace(" ", "-"), "version": "0.1.0", "private": True, "scripts": {}}
        files["package.json"] = json.dumps(pkg, indent=2) + "\n"
    if template in {"docker", "docker-compose"}:
        files["Dockerfile"] = "FROM alpine:3.22\nCMD [\"sh\", \"-c\", \"echo codex-local-ops project\"]\n"
    if template == "docker-compose":
        files["compose.yaml"] = "services:\n  app:\n    build: .\n"
    if template == "rust":
        files["Cargo.toml"] = f'[package]\nname = "{root.name.lower().replace(" ", "_")}"\nversion = "0.1.0"\nedition = "2024"\n'
        files["src/main.rs"] = 'fn main() { println!("hello"); }\n'
    if template == "go":
        files["go.mod"] = f"module {root.name.lower().replace(' ', '-')}\n\ngo 1.24\n"
        files["main.go"] = 'package main\nimport "fmt"\nfunc main(){ fmt.Println("hello") }\n'
    if template == "dotnet":
        files["README.md"] += "\nInitialize with: dotnet new console\n"
    allowed = {"empty", "python", "fastapi", "django", "node", "react", "vite", "nextjs", "docker", "docker-compose", "rust", "go", "dotnet"}
    if template not in allowed:
        raise ValueError(f"Unknown template: {template}")
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {"status": "OK", "path": str(root), "template": template, "files": sorted(files)}


def _default_command(root: Path, action: str) -> list[str] | None:
    info = detect(str(root))
    kinds = set(info["types"])
    if "node" in kinds:
        manager = "npm"
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        scripts = package.get("scripts", {})
        script = "test" if action == "test" else "build"
        if script in scripts:
            return [manager, "run", script]
    if action == "test" and "python" in kinds:
        return ["python", "-m", "pytest"]
    if action == "build" and "python" in kinds:
        return ["python", "-m", "build"]
    if "rust" in kinds:
        return ["cargo", "test" if action == "test" else "build"]
    if "go" in kinds:
        return ["go", "test", "./..."] if action == "test" else ["go", "build", "./..."]
    if "dotnet" in kinds:
        return ["dotnet", "test" if action == "test" else "build"]
    return None


def project_action(path: str, action: str, timeout: int = 900) -> dict:
    root = assert_trusted_path(path, must_exist=True)
    cfg = load_project_config(root)
    spec = cfg.get(action)
    if isinstance(spec, str):
        import shlex
        cmd = shlex.split(spec, posix=True)
    elif isinstance(spec, list):
        cmd = [str(x) for x in spec]
    else:
        cmd = _default_command(root, action)
    if not cmd:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": f"No {action} command configured or detected", "project": str(root)}
    if not shutil.which(cmd[0]):
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": f"Executable not found: {cmd[0]}", "project": str(root)}
    result = run(cmd, cwd=root, timeout=timeout)
    result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"
    return result
