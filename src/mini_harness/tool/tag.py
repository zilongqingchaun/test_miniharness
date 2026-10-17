from dataclasses import dataclass
#工具调用情况
@dataclass(frozen = True)
class Tag:
    SUCCESS: str = 'success'
    INVALID_ARGS: str = 'invalid_args'
    DENIED: str = 'denied'
    EXECUTE_FAILED: str = 'execute_failed'
    DEDUP: str = 'duplicate'
    UNKNOWN_TOOL: str = 'unknown_tool'
    NEED_READ: str = 'need_read'
    STALE: str = 'stale'
    EXISTS: str = 'exists'
    NEED_FULL: str = 'need_full'


#输出情况
@dataclass(frozen=True)
class Outcome:
    COMPLETED:str = "completed"
    ERROR:str = "error"
    EXHAUSTED:str = "exhausted"
    TIMEOUT:str = "timeout"
    INTERRUPTED:str = "interrupted"


OUTCOME = Outcome()
TAG=Tag()