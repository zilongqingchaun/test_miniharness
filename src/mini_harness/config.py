
from dataclasses import dataclass
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv
import os
import fnmatch
@dataclass
class config:
    max_token:int=5000
    history_session_path:str= r'D:\code\project\my_agent\test_miniharness\src\mini_harness\history'
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
    work_space=Path.cwd()
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
    max_read_size:int=5000
    guard_write:bool=True
    sand_box:Path=work_space/'sandbox'
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
        model_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        model_name=os.getenv("MINI_HARNESS_MODEL", "deepseek-v4-flash")
    )

    if not cfg.model_api_key:
        raise ValueError("未配置 DEEPSEEK_API_KEY")

    return cfg

CONFIG=build_config()