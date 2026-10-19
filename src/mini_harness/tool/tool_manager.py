from dataclasses import dataclass
import json
from logging import ERROR
import re

from pathlib import Path
from tkinter import E, EXCEPTION
from mini_harness.tool.tag import TAG
from pydantic import ValidationError
from mini_harness.tool.tools import write_file,edit_file
from mini_harness.tool.utils import digest,render_lines
from mini_harness.config import CONFIG, config

write_tools=[write_file,edit_file]



#返回结果
@dataclass(frozen=True)
class ToolItem:
    content:str=''
    ok:bool=True
    tag:str=''

@dataclass
class record:
    mtime:float
    level:str
    edits:int
    hash:str=''
class Level:
    PARTIAL: str = 'partial'
    FULL: str = 'full'

#实现mark、fresh函数、gate和record，分别实现更新record状态，检查文件是否被外界文件修改，gate判断是否应该修改文件的逻辑，最后execute_tool里面负责调用上面几个函数
#负责管理工具,暂时不实现其他安全性逻辑判断和具体工具实现
class ToolManager:
    def __init__(self,registry:dict,cfg=CONFIG):
        self.registry=registry
        self.files={}
        self.last_tool={}
        self.cfg=cfg
    #更新文件状态
    def mark(self,file:str,level:str):
        p=Path(file)
        
        try:
            mtime=p.stat().st_mtime
            hash=digest(p)
        except OSError:
            return
        old=self.files.get(p)
        if old is not None:
            old_level=old.level
            if level=='full' or old_level=='full':
                return record(mtime=mtime,level='full',edits=old.edits,hash=hash)
            else:
                return record(mtime=mtime,level='partial',edits=old.edits,hash=hash)



        
    def fresh(self,file_path:Path):
        #没有变化返回True
        p=Path(file_path)
        if p.is_file():
            file=self.files.get(p)
            if file is not None:
                if file.mtime == p.stat().st_mtime:
                    return True
                if file.hash==digest(p):
                    return True
        else:
            return False

    def deny(self,file_path,msg:str,tag):
        try:
            body, reach_end=render_lines(val_path=file_path,offset=None,limit=None)
            self.mark(file=file_path,level=Level.FULL if reach_end else Level.PARTIAL)
            return ToolItem(f'[{tag}]: {msg}\n\nCurrent content of {file_path}:\n{body}', False, tag)
        except Exception as e:
            return ToolItem(f'[{tag}]: {msg}\n\n[Counld not display the file: {type(e).__name__}. '
                            f'Read it with read_file before before retrying]', False, tag)



    
    def gate(self,func_name:str,args)->ToolItem|None:
        #需要传入函数名，才能判断需要调用的函数是读还是写
        #之前是否读取过
        file_path=args.file_path
        if file_path not in self.files:
            return None
        #检查文件是否存在
        file_path=Path(file_path)
        if not file_path.is_file():
            return None
        
        #edit需要判断文件是否被更改过，以及是否被读取过
        #write需要判断是否允许重置，是否完整读取过，以及是否被更改过
        if func_name=='edit_file':
            if file_path not in self.files:
                return self.deny(file_path=file_path,msg='文件从未读取过，请读取后再来尝试',tag=TAG.NEED_READ)
            
            if not self.fresh(file_path):
                return self.deny(file_path=file_path,msg='文件已经被更改，请尝试重新读取一遍文件',tag=TAG.STALE)
            
        if func_name=='write_file':
            if not args.over_write:
                return self.deny(file_path=file_path,msg='未批准覆盖',tag=TAG.EXISTS)
            file=self.files.get(file_path)
            if file is None:
                return None
            else:
                level=file.level
            if level!='FULL':
                return self.deny(file_path=file_path,msg='覆盖文件前请完整阅读过整个文件',tag=TAG.NEED_FULL)
            if not self.fresh(file_path):
                return self.deny(file_path=file_path,msg='文件已经被更改，请尝试重新读取一遍文件',tag=TAG.STALE)
        return None

    def Record(self,func:str,args,content):
        if func=='read_file':
             self.mark(file=args.file_path,level=Level.PARTIAL if '[Showing lines ' in content else Level.FULL)

        elif func == 'grep_file':
            for hit in set(re.compile(r'^\[(.+?)\]: \d+: ', re.M).findall(content)):
                self.mark(hit, Level.PARTIAL)
        elif func in write_tools:
            old = self.files.get(args.file_path)
            edits = old.edits if old else 0
            level = Level.FULL if func == 'write_file' else (old.level if old else Level.FULL)

            self.mark(args.file_path, level)
            self.files[args.file_path].edits = edits + 1

            n = self.files[args.file_path].edits
            if self.cfg.thrash_notice and n >= self.cfg.thrash_notice:
                content += (f'\n\n[You have modified this file {n} times without the task passing].\nConsider re-reading it in full, or reconsidering the approach')
        return content        
    
    #传入call
    def execute_tool(self,tool_call):
        name=tool_call.function.name
        definition=self.registry.get(name)
        #未知工具
        if definition is None:
            return ToolItem(content=f'unknown tool: {name}',ok=False,tag=TAG.UNKNOWN_TOOL) 
        try:
            args=json.loads(tool_call.function.arguments or '{}')
            params=definition.parameters(**args)
        except (json.JSONDecodeError,ValidationError,TypeError) as e:
            #参数非法
            return ToolItem(content=f'{type(e).__name__}:{e}',ok=False,tag=TAG.INVALID_ARGS)
        #通过gate才能判断是否执行后续程序,返回None则不管，能否执行由后续判断，返回其他则直接返回



        if self.gate(name,args) is not None:
            return self.gate(name,args)

        
        try:
            result=definition.function(params)
        except Exception as e:
            return ToolItem(content=f'{type(e).__name__}:{e}',ok=False,tag=TAG.EXECUTE_FAILED)
        raw=result if isinstance(result,str) else json.dumps(result,ensure_ascii=False)
        raw=self.Record(name,args=args,content=raw)
        return ToolItem(content=raw,ok=True,tag=TAG.SUCCESS)

