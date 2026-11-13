"""Local two-phase SWE-bench Verified subset."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import tempfile

from bench.sb_cli import ADAPTER, instance_id_for_trial, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOS = ("django__django", "sympy__sympy", "pytest-dev__pytest", "sphinx-doc__sphinx",
         "scikit-learn__scikit-learn", "matplotlib__matplotlib")
UV_INSTALL = re.compile(r"(?m)^RUN curl -LsSf https://astral\.sh/uv/0\.7\.13/install\.sh \| sh[^\r\n]*")
BASE = re.compile(r"(?m)^FROM\s+(swebench/\S+)")


def image_ready(entry):
    return subprocess.run(
        ["docker", "image", "inspect", "mini-harness-prewarm:" + entry["id"]],
        capture_output=True,
    ).returncode == 0


def prepare(root):
    verify = root / "verification"
    plan = verify / "verified30"
    manifest = plan / "manifest.json"
    if manifest.exists():
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in entries:
            entry["path"] = str(plan / "tasks" / entry["id"])
        return entries
    groups = {repo: [] for repo in REPOS}
    template = verify / "swe-offline-uv/django__django-11099/environment"
    if not (template / "Dockerfile").is_file() or not (verify / "verified-source").is_dir():
        raise SystemExit(f"Missing prepared benchmark inputs under {verify}. "
                         "See README.md or pass --root pointing to your existing benchmark data.")
    warm = (template / "Dockerfile").read_text(encoding="utf-8").split(
        "# mini-harness dependency prewarm", 1)[1]
    for config in sorted((verify / "verified-source").rglob("task.toml")):
        task = config.parent
        repo = task.name.rsplit("-", 1)[0]
        dockerfile = task / "environment/Dockerfile"
        if repo not in groups or not dockerfile.is_file():
            continue
        body = dockerfile.read_text(encoding="utf-8")
        if BASE.search(body) and UV_INSTALL.search(body):
            groups[repo].append((task, body))
    rng = random.Random(20261110)
    entries = []
    for row in zip(*(rng.sample(groups[repo], 5) for repo in REPOS)):
        for source, body in row:
            task = plan / "tasks" / source.name
            shutil.copytree(source, task, dirs_exist_ok=True)
            env_dir = task / "environment"
            for name in ("uv", "uvx", "harness-pyproject.toml"):
                shutil.copy2(template / name, env_dir / name)
            image = BASE.search(body).group(1)
            body = UV_INSTALL.sub("COPY --chmod=0755 uv uvx /root/.local/bin/", body, count=1)
            (env_dir / "Dockerfile").write_text(body + "\n" + warm, encoding="utf-8", newline="\n")
            entries.append({"id": source.name, "image": image, "path": str(task)})
    write_json(manifest, [{key: entry[key] for key in ("id", "image")} for entry in entries])
    return entries


def prefetch(entries, root):
    for i, entry in enumerate(entries, 1):
        tag = "mini-harness-prewarm:" + entry["id"]
        if image_ready(entry):
            continue
        if shutil.disk_usage(root).free < 20 * 1024**3:
            raise SystemExit(f"Prefetch paused: the filesystem containing {root} has under 20 GiB free.")
        print(f"[{i}/{len(entries)}] {entry['id']}", flush=True)
        subprocess.run(["docker", "pull", entry["image"]], check=True)
        subprocess.run(["docker", "build", "-t", tag, str(Path(entry["path"]) / "environment")],
                       check=True)


def run(entries, args):
    phase = args.phase
    index = {}
    agent = {"import_path": ADAPTER, "model_name": "deepseek/deepseek-v4-flash",
             "kwargs": {"provider": "deepseek", "max_seconds": 300}}
    if phase == "score":
        if not args.predictions:
            raise SystemExit("Pass the generation job directory with --predictions.")
        for directory in args.predictions:
            for file in directory.glob("*/agent/prediction.json"):
                prediction = json.loads(file.read_text(encoding="utf-8"))
                if prediction["instance_id"] in index:
                    raise ValueError(f"Duplicate prediction: {prediction['instance_id']}")
                index[prediction["instance_id"]] = str(file.resolve())
        entries = [entry for entry in entries if entry["id"] in index]
        if not entries:
            raise SystemExit("No saved predictions found.")
        agent = {"import_path": "bench.batch30:ReplayPatch"}
    missing = []
    for entry in entries:
        tag = "mini-harness-prewarm:" + entry["id"]
        if not image_ready(entry):
            missing.append(entry["id"])
            continue
        task_config = Path(entry["path"]) / "task.toml"
        body = task_config.read_text(encoding="utf-8")
        setting = f'docker_image = "{tag}"'
        if setting not in body:
            body = body.replace("[environment]\n", "[environment]\n" + setting + "\n", 1)
            task_config.write_text(body, encoding="utf-8", newline="\n")
    if missing:
        raise SystemExit(f"{len(missing)} task images are not ready; finish prefetch first: {', '.join(missing)}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = args.root / "bench-results"
    job = results / f"verified30-{phase}-{stamp}"
    cfg = {
        "job_name": job.name, "jobs_dir": str(results), "n_attempts": 1,
        "n_concurrent_trials": args.jobs, "retry": {"max_retries": 0},
        "environment": {"type": "docker", "delete": True},
        "verifier": {"disable": phase == "generate"},
        "agents": [agent], "tasks": [{"path": entry["path"]} for entry in entries],
    }
    config_path = job.with_suffix(".harbor.json")
    if index:
        index_path = job.with_suffix(".patches.json")
        write_json(index_path, index)
        os.environ["HARNESS_REPLAY_INDEX"] = str(index_path)
    write_json(config_path, cfg)
    print(f"{phase}: {len(entries)}/30 tasks; results: {job}", flush=True)
    subprocess.run(["harbor", "run", "--config", str(config_path)], check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("prepare", "prefetch", "status", "generate", "score"))
    p.add_argument("--root", type=Path, default=PROJECT_ROOT,
                   help="Benchmark data and output root (default: project directory)")
    p.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    p.add_argument("--predictions", type=Path, action="append")
    p.add_argument("--batch", type=int, choices=(1, 2, 3), help="Optional fixed group of ten")
    args = p.parse_args()
    args.root = args.root.expanduser().resolve()
    env = os.environ
    tool_dirs = [args.root / "docker-desktop/resources/bin", args.root / "harbor-venv/Scripts"]
    env["PATH"] = os.pathsep.join([*(str(path) for path in tool_dirs if path.is_dir()), env["PATH"]])
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PYTHONUTF8"] = "1"
    entries = prepare(args.root)
    if len(entries) != 30 or len({e["id"] for e in entries}) != 30:
        raise SystemExit("Invalid fixed 30-task manifest.")
    if args.batch:
        start = (args.batch - 1) * 10
        entries = entries[start:start + 10]
    if args.phase == "prepare":
        print(args.root / "verification/verified30/manifest.json")
    elif args.phase == "prefetch":
        prefetch(entries, args.root)
    elif args.phase == "status":
        count = sum(image_ready(entry) for entry in entries)
        free = shutil.disk_usage(args.root).free / 1024**3
        print(f"Images ready: {count}/{len(entries)}; {free:.1f} GiB free on the benchmark root filesystem.")
    else:
        run(entries, args)


def __getattr__(name):
    if name != "ReplayPatch":
        raise AttributeError(name)
    from harbor.agents.nop import NopAgent

    class ReplayPatch(NopAgent):
        async def run(self, instruction, environment, context):
            task_id = instance_id_for_trial(Path(self.logs_dir).parent)
            index = json.loads(Path(os.environ["HARNESS_REPLAY_INDEX"]).read_text(encoding="utf-8"))
            prediction = json.loads(Path(index[task_id]).read_text(encoding="utf-8"))
            if not prediction["model_patch"]:
                return
            with tempfile.TemporaryDirectory() as folder:
                patch = Path(folder) / "prediction.patch"
                patch.write_bytes(prediction["model_patch"].encode("utf-8"))
                await environment.upload_file(patch, "/tmp/harness-replay.patch")
            result = await environment.exec(
                command="git apply --check /tmp/harness-replay.patch && git apply /tmp/harness-replay.patch",
                cwd="/testbed",
            )
            if result.return_code:
                raise RuntimeError(result.stderr or result.stdout or "Could not apply prediction")
    return ReplayPatch


if __name__ == "__main__":
    main()

