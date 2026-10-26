from ast import pattern
from calendar import c
import fnmatch
from genericpath import isfile
from hmac import new
import random
import os
import shutil
import subprocess
from turtle import st
from openai import OpenAI
from pydantic import BaseModel, Field,ConfigDict,field_validator,model_validator
from typing import Optional, List, Dict, Any
from pathlib import Path
import glob
import re
from pydantic.type_adapter import P
from mini_harness.config import CONFIG,config
from mini_harness.tool.utils import is_denied,render_lines,atomic_write,_split_lines,LINE_NO,strip_line_no,TODO,SUBAGENT,to_api_tool
from enum import Enum
import uuid
import threading
from mini_harness.tool.box import TOOLS
import time
from openai import OpenAI
from mini_harness.tool.tool_manager import ToolManager
from mini_harness.agent import retry_call
# 1. glob_file 
class GlobFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    pattern: str = Field(..., description="The glob pattern to match files, e.g., '*.py',或者src/*.py,即如果指定路径就检索路径下的py文件，不然递归检索所有py文件")
    path:Path=Field(Path('.'),description='要检索的文件夹路径')

def glob_file(globfileinput: GlobFileInput,cfg:config)->str:
    pattern=globfileinput.pattern
    path=globfileinput.path
    workspace = Path(cfg.work_space).resolve()
    if not path.is_absolute():
        path=cfg.work_space/path
    path = path.resolve()
    if not cfg.guard_read:
        pass
    else:
        if not path.is_relative_to(cfg.work_space):
            raise PermissionError(f"{path}不在工作区，拒绝访问")
        if is_denied(file_path=path,cfg=cfg):
            raise PermissionError(f"{path}中包含拒绝访问的目录，拒绝访问")

    #根据path获取绝对路径
    if '/' not in pattern:
        pattern='**/'+pattern
    matches=[]
    pattern = pattern.replace('\\', '/')
    #增加检查，可能越过工作区
    if Path(pattern).is_absolute() or '..' in Path(pattern).parts:
        raise ValueError("非法的 glob pattern")
    for file in glob.glob(pattern, root_dir = path, recursive = True):
        full=path/file
        full=full.resolve()
        if not full.is_file():
            continue
        if not cfg.guard_read:
            pass
        else:
            if not full.is_relative_to(cfg.work_space):
                continue
            if is_denied(file_path=full,cfg=cfg):
                continue

        matches.append(full)

    return '\n'.join(str(file) for file in matches) if matches else 'no match'




# 2. grep_file
class GrepFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    pattern: str = Field(..., description="The string or regex pattern to search for")
    path: str = Field(default=".", description="The directory or file path to search in")
    glob:str=Field(...,description='搜索的格式如 *.py')
    @field_validator('pattern')
    @classmethod
    def test_regx(cls,pattern:str):
        try:
            re.compile(pattern=pattern)
        except re.error as e:
            raise ValueError(f"re.error,{e}")
        return pattern


def grep_file(grepfileinput: GrepFileInput,cfg:config)->str:
    #读取文件路径（传入的可能是文件或者文件夹）
    path=Path(grepfileinput.path)
    glob=grepfileinput.glob
    truncated = False
    work_space=cfg.work_space
    if not path.is_absolute():
        path=cfg.work_space/path
    path = path.resolve()
    if not cfg.guard_read:
        pass
    else:
        if not path.is_relative_to(cfg.work_space):
            raise PermissionError(f"{path}不在工作区，拒绝访问")
        if is_denied(file_path=path,cfg=cfg):
            raise PermissionError(f"{path}中包含拒绝访问的目录，拒绝访问")

    regex=re.compile(pattern=grepfileinput.pattern)
    targets=[]
    hits=[]
    if path.is_file():
        targets.append(path)
    else:
        #遍历路径
        for root,dirs,files in os.walk(path):
            #筛选路径
            dirs[:]=[dir for dir in dirs if not dir.startswith('.') and dir not in cfg.deny_dir]

            for file in files:
                file_path=Path(root)/file
                file_path=file_path.resolve()

                if not cfg.guard_read:
                    pass
                else:
                    if not file_path.is_relative_to(cfg.work_space):
                        continue
                    if is_denied(file_path=file_path,cfg=cfg):
                        continue

                if glob and not fnmatch.fnmatch(file,glob):
                        continue
                targets.append(file_path)

    for file_path in targets:
        try:
            if truncated:
                break
            with open(file=file_path,encoding='utf-8',mode='r',errors='replace') as f:
                for line_index,line in enumerate(f,1):

                    if regex.search(line):
                        hits.append(f'匹配内容：{file_path},{line_index}:{line.rstrip()}')
                    if len(hits)>=cfg.max_grep_num:
                        truncated=True
                        break
        except (OSError, UnicodeDecodeError):
            continue
        if truncated:
            hits.append(f'\n..... truncated at {cfg.max_grep_num}')
    return '\n'.join(hits) if hits else 'no match'






    


# 3. read_file
class ReadFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    path: str = Field(..., description="The path to the file to read")
    offset: Optional[int] = Field(None, description="Line number to start reading from (1-indexed)")
    limit: Optional[int] = Field(None, description="Maximum number of lines to read")

def read_file(readfileinput: ReadFileInput,cfg:config)->str:
    #获取文件路径
    path=Path(readfileinput.path)
    workspace = Path(cfg.work_space).resolve()
    if not path.is_absolute():
        path=cfg.work_space/path
    path = path.resolve()
    if not cfg.guard_read:
        pass
    else:
        if not path.is_relative_to(cfg.work_space):
                raise PermissionError(f"{path}不在工作区，拒绝访问")
        if is_denied(file_path=path,cfg=cfg):
                raise PermissionError(f"{path}中包含拒绝访问的目录，拒绝访问")
    if not path.is_file():
        raise FileNotFoundError('这不是一个文件')
    #判断是全部读取
    if readfileinput.offset is None and readfileinput.limit is None:
        size=path.stat().st_size
        if size >=cfg.max_read_size:
            raise ValueError(f'文件size:{size},最大size:{cfg.max_read_size}.文件太大超过读取限制')
    body,_=render_lines(file_path=path,offset=readfileinput.offset,limit=readfileinput.limit,cfg=cfg)
    return body


# 4. write_file
class WriteFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    path: str = Field(..., description="The path where the new file will be created")
    content: str = Field(..., description="The full content to write into the file")
    overwrite: bool = Field(default=False, description="Set to true to overwrite an existing file (requires prior full read) false新建一个文件而非覆盖当前文件")

def write_file(writefileinput: WriteFileInput,cfg:config):
    path=Path(writefileinput.path)
    workspace = Path(cfg.work_space).resolve()
    if not path.is_absolute():
        path=cfg.work_space/path
    path = path.resolve()
    if not cfg.guard_write:
        pass
    else:
        if not path.is_relative_to(cfg.sand_box):
            raise PermissionError(f"{path}不在sandbox里,不允许执行")
    existed=path.is_file()
    atomic_write(path=path,content=writefileinput.content)
    status='overwrite' if existed else 'create new'
    n=len(_split_lines(content=writefileinput.content))
    return f'{status},文本行数，{n}'


# 5. edit_file

class EditFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    path: str = Field(..., description="The path to the file to edit")
    old_string: str = Field(..., description="The exact text to be replaced (without line number prefixes)")
    new_string: str = Field(..., description="The new text to replace the old_string with(without line number prefixes)")
    replace_all:bool=Field(False,description='如果有多处old_string,是否全部替换。')
    @model_validator(mode = 'after')
    def test_content(self):
        if self.old_string == self.new_string:
            raise ValueError(f'[content error]: the old content is equal to the new content')
        return self


def edit_file(editfileinput: EditFileInput,cfg:config):
    path=Path(editfileinput.path)
    workspace = Path(cfg.work_space).resolve()
    if not path.is_absolute():
        path=cfg.work_space/path
    path = path.resolve()
    if not cfg.guard_write:
        pass
    else:
        if not path.is_relative_to(cfg.sand_box):
            raise PermissionError(f"{path}不在sandbox里,不允许执行")
    if not path.is_file():
        raise ValueError(f'{path}不是一个文件，请传入正确文件') 
    
    try:
        content=path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        raise ValueError(f'{path}非utf-8编码')

    old_string=editfileinput.old_string
    new_string=editfileinput.new_string

    if not old_string in content:
        #尝试去除行号
        old_string=strip_line_no(old_string)
        if not old_string in content:
            raise ValueError(f'{editfileinput.old_string}不在{editfileinput.path}中，请核实')
        if any(LINE_NO.match(line) for line in _split_lines(new_string)):
            raise ValueError(f'{editfileinput.new_string}中存在行号，请核实')
        if old_string==new_string:
            raise ValueError(f'{editfileinput.new_string}和{editfileinput.old_string}相等')

    if not editfileinput.replace_all and content.count(old_string)>1:
        raise ValueError(f'{editfileinput.old_string}多次出现')

    # chars=content.index(old_string)
    # line_index=content[:chars].count('\n')+1
    old_n=len(_split_lines(old_string))
    new_n=len(_split_lines(new_string))
    hits=content.count(old_string)
    content= content.replace(old_string,new_string) if editfileinput.replace_all else content.replace(old_string,new_string,1)
    total_n=len(_split_lines(content))
    atomic_write(path=path,content=content)
    return f'已编辑，旧内容{old_n}行，新内容{new_n}，一共{total_n}行'

   

# 6. run_bash
class RunBashInput(BaseModel):
    model_config = ConfigDict(extra = 'forbid')
    command: str = Field(..., description="The bash command to execute on the host terminal")
    
def run_bash(runbashinput: RunBashInput,cfg:config):
    try:
        result=subprocess.run(runbashinput.command,
            shell = True,
            capture_output=True,
            text=True,
            timeout = cfg.bash_timeout,
            env = cfg.bash_env,
            cwd = cfg.work_space
                              )
    except subprocess.TimeoutExpired as e:
        raise TimeoutError(f'[timeout]:终端运行超时，{e}')
    part=[]
    if result.stdout:
        part.append(f'[stdout]:{result.stdout}')
    if result.stderr:
        part.append(f'[stderr]:{result.stderr}')
    if result.returncode!=0:
        part.append(f'exit code:{result.returncode}')
    content='\n'.join(part) if part else 'no result'
    return content

# 7. run_sandbox
class RunSandboxInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command: str = Field(description='Shell command inside an isolated Python container. sandbox/ is mounted at /workspace; use paths relative to it.')
    timeout_seconds: int = Field(default=30, ge=1, le=300, strict=True, description='Maximum command runtime in seconds.')




def run_sandbox(runsandboxinput: RunSandboxInput,cfg:config):
    docker=shutil.which('docker')
    if not docker:
        raise RuntimeError('run_sandbox need Docker,请安装docker')
    root=cfg.work_space.resolve()
    workspace=cfg.sand_box
    if workspace.is_symlink() or not workspace.resolve().is_relative_to(root)
        raise PermissionError(f'{workspace}必须是{root}下的真实路径')
    workspace = workspace.resolve()
    if ',' in str(workspace):
        raise ValueError('Docker sandbox paths cannot contain commas')

    name='mini-harness'+uuid.uuid4().hex
    runner = (
        'import subprocess,sys\n'
        'try:\n'
        ' p=subprocess.run(sys.argv[1],shell=True,timeout=int(sys.argv[2]))\n'
        ' sys.exit(p.returncode if p.returncode >= 0 else 128-p.returncode)\n'
        'except subprocess.TimeoutExpired:\n'
        ' print("[sandbox command timed out]",flush=True)\n'
        ' sys.exit(124)\n'
    )
    command = [
            docker, 'run', '--rm', '--pull=never', '--name', name,
            '--label', 'mini-harness.sandbox=true', '--network=none', '--read-only',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=64',
            '--memory=256m', '--memory-swap=256m', '--cpus=1', '--log-driver=none',
            '--ulimit', 'fsize=16777216:16777216', '--user', f'{os.getuid()}:{os.getgid()}',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--mount', f'type=bind,src={workspace},dst=/workspace', '--workdir', '/workspace',
            '--env', 'HOME=/tmp', '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--entrypoint', 'python', 'python:3.12-slim', '-u', '-c', runner,
            runsandboxinput.command, str(runsandboxinput.timeout_seconds),
        ]
    output=bytearray()
    clipped=False
    def drain(pipe):
        nonlocal clipped
        with pipe:
            while chunk := pipe.read(8192):
                remaining = max(0, cfg.bash_limit - len(output))
                output.extend(chunk[:remaining])
                clipped |= len(chunk) > remaining

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, env=cfg.bash_env, start_new_session=True)
    reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
    reader.start()
    try:
        process.wait(timeout=runsandboxinput.timeout_seconds + 10)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError('Docker sandbox startup or execution timed out') from error
    finally:
        try:
            cleanup = subprocess.run([docker, 'rm', '-f', name], capture_output=True,
                                     timeout=5, env=cfg.bash_env)
            if cleanup.returncode and b'No such container' not in cleanup.stderr:
                raise RuntimeError(f'Could not remove sandbox container {name}: ' + cleanup.stderr.decode(errors='replace'))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=1)
    text = output.decode('utf-8', errors='replace') or 'no result'
    if clipped:
        text += f'\n[output truncated at {cfg.bash_limit} bytes]'
    if process.returncode:
        if process.returncode == 124:
            raise TimeoutError(f'Sandbox exceeded {runsandboxinput.timeout_seconds}s.\n{text}')
        raise RuntimeError(f'Sandbox exited with code {process.returncode}.\n{text}')
    return text


# 8. run_todo


class StatusItem(str, Enum):
    completed = 'completed'
    pending = 'pending'
    in_progress = 'in_progress'
class TodoItem(BaseModel):
    model_config = ConfigDict(extra = 'forbid')
    task: str = Field(..., description="todo list 中任务的文本")
    activeform:str=Field(description='任务的激活形式,如fixing the bug')
    status: StatusItem = Field(..., description="Status of the task: 'pending', 'in_progress', 'completed'")

class RunTodoInput(BaseModel):
    model_config = ConfigDict(extra = 'forbid')
    todos: List[TodoItem] = Field(..., description="list of todo items")
    @model_validator(mode = 'after')
    def test_status(self):
        in_progress_count = sum(1 for t in self.todos if t.status == 'in_progress')
        if in_progress_count > 1:
            raise ValueError(f'[invalid status]: there is only one thing can be in progress')
        return self
    
def run_todo(runtodoinput: RunTodoInput,cfg:config):
    return TODO.update(runtodoinput.todos)
    


# 9. run_subagent
class AgentType(str, Enum):
    explore_agent = 'explore_agent'
    coding_agent = 'coding_agent'
    planning_agent = 'planning_agent'

class RunSubAgentInput(BaseModel):
    model_config = ConfigDict(extra = 'forbid')
    task_description: str = Field(..., description="A detailed description of the task the subagent needs to accomplish")
    prompt:str=Field(..., description="发给subagent的提示词。")
    agent_type:AgentType=Field(description = 'the agent type which you want use, including explore_agent(explore, read and find the file and file content based on the task), coding_agent (create, code and program the file based on the task) and planning_agent (read, analyze and make a plan to better finish the task)')
def run_subagent(runsubagentinput: RunSubAgentInput,cfg:config):
    full_tool_list = to_api_tool(TOOLS)
    config = SUBAGENT.agent_table.get(runsubagentinput.agent_type.value)
    if config is None:
        raise ValueError(f'[invalid subagent]: invalid subagent {runsubagentinput.agent_type.value}')
    tool_list = [t for t in full_tool_list if t['function']['name'] in config['tools']]
    regis = {t.name: t for t in TOOLS if t.name in config['tools']}
    tool_count = 0
    sub_message = [
        {
            'role': 'system',
            'content': f'''
You are my subagent-{runsubagentinput.agent_type.value}, your responsibility is to {config['description']}
{config['prompt']}
completed the task by giving the summary and analyzing report
'''
        },
        {
            'role': 'user',
            'content': runsubagentinput.prompt
        }
    ]
    start = time.time()
    print(f'[{runsubagentinput.agent_type.value}]: {runsubagentinput.task_description}')
    client = OpenAI(
        api_key = cfg.model_api_key,
        base_url = cfg.base_url,
        max_retries = 0
    )
    executer = ToolManager(regis,cfg = cfg)
    for turn in range(cfg.max_turn):
        response = retry_call(lambda: client.chat.completions.create(
            **cfg.request_options(sub=True),
            messages = cfg.request_messages(sub_message),
            tools = tool_list,
            stream = False
        ), cfg = cfg)
        message = response.choices[0].message
        if message.tool_calls:
            d = message.model_dump(exclude_none = True)
            sub_message.append(d)
            for tool_call in message.tool_calls:
                res = executer.execute_tool(tool_call)
                
                if len(res.content) < cfg.clip_limit:
                    content=res.content
                else:
                    content=res.content[:cfg.clip_limit] + f'\n.....clipped at {cfg.clip_limit} of {len(res.content)} chars'
                tool_count += 1
                sub_message.append(
                    {'role': 'tool', 'tool_call_id': tool_call.id, 'content': content}
                )
        else:
            sub_message.append(
                message.model_dump(exclude_none=True)
            )
            end = time.time() -start
            print(f'[{runsubagentinput.agent_type.value}]: {runsubagentinput.task_description} -- {tool_count} tools -- {end:.1f}s')
            return message.content
    else:
        return f'[agent done]: the agent run out of the turns for the actions, the task is incompleted'