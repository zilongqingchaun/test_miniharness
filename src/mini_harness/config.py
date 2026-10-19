
from dataclasses import dataclass
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv
import os
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