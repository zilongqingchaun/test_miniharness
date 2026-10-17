from dataclasses import dataclass
import json
from mini_harness.tool.tag import TAG

from pydantic import ValidationError
#返回结果
@dataclass(frozen=True)
class ToolItem:
    content:str=''
    ok:bool=True
    tag:str=''

#负责管理工具,暂时不实现其他安全性逻辑判断和具体工具实现
class ToolManager:
    def __init__(self,registry:dict,cfg=None):
        self.registry=registry

    #传入call
    def execute_tool(self,tool_call):
        name=tool_call.function.name
        definition=self.registry.get(name)
        if definition is None:
            return ToolItem(content=f'unknown tool: {name}',ok=False,tag=TAG.UNKNOWN_TOOL)
        try:
            args=json.loads(tool_call.function.arguments or '{}')
            params=definition.parameters(**args)
        except (json.JSONDecodeError,ValidationError,TypeError) as e:
            return ToolItem(content=f'{type(e).__name__}:{e}',ok=False,tag=TAG.INVALID_ARGS)
        try:
            result=definition.function(params)
        except Exception as e:
            return ToolItem(content=f'{type(e).__name__}:{e}',ok=False,tag=TAG.EXECUTE_FAILED)
        raw=result if isinstance(result,str) else json.dumps(result,ensure_ascii=False)
        return ToolItem(content=raw,ok=True,tag=TAG.SUCCESS)

