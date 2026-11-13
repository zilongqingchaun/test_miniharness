
from dataclasses import dataclass
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv
import os
import fnmatch
@dataclass
class config:
    max_token:int=10000
    history_session_path: str = '.runs'
    user_name:str='the_user'
    agent_task_mode:bool=True
    base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-v4-flash"
    model_api_key: str = field(default="", repr=False)
    max_turn:int=50
    max_run_time:float=600
    max_retry :int=3
    rate_retry:int=5
    rate_base:int=3
    retry_base:int=3
    rate_cap:int=3
    thrash_notice:int=5000
    read_limit:int=50000
    over_write:bool=True
    work_space: Path = field(default_factory=Path.cwd)
    deny_name: tuple = (
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "id_rsa*",
            "id_ed25519*",
            "id_ecdsa*",
            ".netrc",
            ".npmrc",
            ".pypirc",
            "*credential*",
            "*secret*",
        )
    deny_dir: frozenset = frozenset({".ssh", ".aws", ".gnupg"})
    guard_read:bool=True
    bash_limit: int = 30000
    max_grep_num:int=100
    clip_limit: int = 80000
    diff_echo_lines: int = 40
    max_read_size:int=5000
    guard_write:bool=True
    sand_box: Path | None = None
    bash_timeout: int = 30

    bash_env_deny: tuple = (
        '*KEY*',
        '*TOKEN*',
        '*SECRET*',
        '*PASSWORD*',
        '*CREDENTIAL*',
        '*_PWD',
        '*AUTH*'
    )

    risky_policy: str = 'deny'
    approval: object = field(default=None, repr=False)
    deadline: float | None = field(default=None, repr=False)
    context_chars: int = 160000
    recent_keep: int = 12
    api_timeout: float = 60
    preserve_reasoning: bool = True
    run_id: str = ''
    child_results: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.work_space = Path(self.work_space).resolve()
        self.sand_box = Path(self.sand_box).resolve() if self.sand_box else self.work_space / 'sandbox'
        if self.risky_policy not in {'deny', 'allow', 'ask'}:
            raise ValueError('Invalid risky_policy')
        if self.max_run_time < 0 or self.api_timeout <= 0 or self.clip_limit < 1:
            raise ValueError('Invalid runtime/output limits')
        if min(self.max_turn, self.max_token, self.max_retry, self.rate_retry) < 1:
            raise ValueError('Turns, tokens and retries must be positive')

    def remaining(self, cap=None):
        import time
        remaining = float('inf') if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Run budget exhausted')
        return min(remaining, cap) if cap is not None else remaining

    def request_options(self, sub=False):
        return dict(model=self.model_name, max_tokens=self.max_token, temperature=0,
                    timeout=self.remaining(self.api_timeout))

    def request_messages(self, messages):
        excluded = {'annotations', 'parsed'}
        if not self.preserve_reasoning:
            excluded.add('reasoning_content')
        return [{k: v for k, v in m.items() if k not in excluded} for m in messages]

    @property
    def bash_env(self) -> dict:
        secrets = {'MINI_HARNESS_MODEL', 'DEEPSEEK_API_KEY', 
                   }
        return {
            key: value for key, value in os.environ.items()
            if key.upper() not in secrets
            and not any(fnmatch.fnmatch(key.upper(), name) for name in self.bash_env_deny)
        }




def build_config() -> config:

    # 根据你的目录结构，找到项目根目录
    root = Path(__file__).resolve().parents[2]

    # 加载.env
    load_dotenv(root / ".env")

    # 从环境变量中读取配置
    cfg = config(
        model_api_key=os.getenv("MINI_HARNESS_API_KEY") or os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("MINI_HARNESS_BASE_URL", "https://api.deepseek.com"),
        preserve_reasoning=os.getenv("MINI_HARNESS_PROVIDER", "deepseek") == "deepseek",
        model_name=os.getenv("MINI_HARNESS_MODEL", "deepseek-v4-flash")
    )


    return cfg

CONFIG=build_config()