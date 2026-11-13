import math
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import override

from dotenv import load_dotenv
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from bench import atif

REPO_ROOT = Path(__file__).resolve().parent.parent
GUEST_PKG = "/opt/mini-harness"
GUEST_SECRET = "/opt/mini_harness_secret"
KEY_FILE = f"{GUEST_SECRET}/api.key"
LOG_DIR = "/logs/agent"
GUEST_PYTHON = f'{GUEST_PKG}/.venv/bin/python'
load_dotenv(REPO_ROOT / ".env")
AGENT_VERSION = atif.git_version(REPO_ROOT)


class MiniHarnessAgent(BaseInstalledAgent):
    SUPPORTS_ATIF: bool = True

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        max_seconds: float | None = None,
        repo_dir: str = "/testbed",
        **kwargs,
    ):
        settings = {**os.environ, **(kwargs.get("extra_env") or {})}
        model = model_name or settings.get("MINI_HARNESS_MODEL")
        prefix = model.partition("/")[0] if model else None
        inferred = (
            prefix
            if prefix in {"openai", "deepseek"}
            else (
                "deepseek" if not model or model.startswith("deepseek-") else "openai"
            )
        )
        provider = (
            provider or settings.get("MINI_HARNESS_PROVIDER") or inferred
        ).lower()
        if provider not in {"openai", "deepseek"}:
            raise ValueError(f"Unsupported provider: {provider}")
        if prefix in {"openai", "deepseek"}:
            if prefix != provider:
                raise ValueError("Model prefix conflicts with provider")
            model = model.partition("/")[2]
        if model is not None and not model.strip():
            raise ValueError("model_name must not be empty")
        self._model_id = model or (
            "gpt-4.1" if provider == "openai" else "deepseek-v4-flash"
        )
        key_env = (
            api_key_env
            or settings.get("MINI_HARNESS_API_KEY_ENV")
            or f"{provider.upper()}_API_KEY"
        )
        self._api_key = settings.get("MINI_HARNESS_API_KEY") or settings.get(key_env)
        if not self._api_key:
            raise RuntimeError(f"[adapter]: set {key_env} or MINI_HARNESS_API_KEY")
        self.repo_dir = repo_dir
        self.max_seconds = float(
            max_seconds if max_seconds is not None
            else settings.get("MINI_HARNESS_WALL_BUDGET", 600)
        )
        if not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("max_seconds must be finite and positive")
        default_url = (
            settings.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            if provider == "openai" else "https://api.deepseek.com"
        )
        self._run_env = {
            "MINI_HARNESS_PROVIDER": provider,
            "MINI_HARNESS_MODEL": self._model_id,
            "MINI_HARNESS_BASE_URL": base_url or settings.get("MINI_HARNESS_BASE_URL") or default_url,
            "PYTHONUNBUFFERED": "1",
        }
        super().__init__(
            logs_dir=logs_dir, model_name=f"{provider}/{self._model_id}", **kwargs
        )

    @staticmethod
    @override
    def name() -> str:
        return "mini-harness"

    @override
    def get_version_command(self) -> str | None:
        return None

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        result = await self.exec_as_root(
            environment,
            command=(
                "if ! command -v curl >/dev/null || "
                "[ ! -s /etc/ssl/certs/ca-certificates.crt ]; then "
                "apt-get update && apt-get install -y curl ca-certificates; fi"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        if result.return_code:
            raise RuntimeError("Could not install container prerequisites")
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "pkg"
            stage.mkdir()
            shutil.copy(REPO_ROOT / "pyproject.toml", stage)
            shutil.copytree(
                REPO_ROOT / "src", stage / "src",
                ignore=shutil.ignore_patterns("__pycache__", "history", "*.pyc", ".env*"),
            )
            if (REPO_ROOT / "README.md").exists():
                shutil.copy(REPO_ROOT / "README.md", stage)
            await environment.upload_dir(stage, GUEST_PKG)
        result = await self.exec_as_root(
            environment,
            command=(
                "set -euo pipefail; "
                'export PATH="$HOME/.local/bin:$PATH"; '
                "if ! command -v uv >/dev/null; then "
                "curl -LsSf https://astral.sh/uv/install.sh | sh; fi; "
                f"uv venv --offline --python 3.12 {GUEST_PKG}/.venv || "
                f"uv venv --python 3.12 {GUEST_PKG}/.venv; "
                f"uv pip install --offline --python {GUEST_PYTHON} {GUEST_PKG} || "
                f"uv pip install --python {GUEST_PYTHON} {GUEST_PKG}; "
                f"{GUEST_PYTHON} -m mini_harness.main --help"
            ),
        )
        if result.return_code:
            raise RuntimeError("Could not install test mini-harness in the container")

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        try:
            d = Path(self.logs_dir)
            traj = atif.build(d, version=AGENT_VERSION, model_name=self._model_id)
            if traj is None:
                print(
                    f"[adapter]: no trajectory for {d.parent.name} (missing or empty session)"
                )
                return
            atif.write_trajectory(d, traj)
            fm = traj.final_metrics
            if fm is not None:
                context.n_input_tokens = fm.total_prompt_tokens
                context.n_output_tokens = fm.total_completion_tokens
        except Exception as e:
            print(
                f"[adapter]: trajectory conversion failed for {self.logs_dir}: {type(e).__name__}: {e}"
            )

    @override
    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        # Stage the credential only for launch, then unlink it before the agent starts.
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret"
            secret.mkdir()
            (secret / "api.key").write_text(self._api_key, encoding="utf-8")
            await environment.upload_dir(secret, GUEST_SECRET)
        argv = [
            GUEST_PYTHON, "-m", "mini_harness.main",
            "--task", instruction,
            "--workspace", self.repo_dir,
            "--write-workspace", "--allow-risky",
            "--history-dir", LOG_DIR,
            "--run-id", "mini_harness",
            "--telemetry-out", f"{LOG_DIR}/mini_harness_tele.json",
            "--max-seconds", str(self.max_seconds),
        ]
        result = await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f'export MINI_HARNESS_API_KEY="$(cat {KEY_FILE})"; '
                f"rm -f {KEY_FILE}; "
                'test -n "$MINI_HARNESS_API_KEY"; '
                f"mkdir -p {LOG_DIR}; "
                f"{shlex.join(argv)} 2>&1 | tee {LOG_DIR}/mini_harness.txt"
            ),
            cwd=self.repo_dir,
            env=self._run_env,
        )
        if result.return_code:
            raise RuntimeError(f"Agent exited with code {result.return_code}")
