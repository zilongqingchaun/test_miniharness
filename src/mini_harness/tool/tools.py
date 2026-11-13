import fnmatch
import glob
import os
import re
import shutil
import subprocess
import threading
import uuid
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mini_harness.config import config
from mini_harness.tool.utils import (
    LINE_NO,
    SUBAGENT,
    _split_lines,
    atomic_write,
    is_denied,
    render_lines,
    strip_line_no,
)
# 1. glob_file 
class GlobFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    pattern: str = Field(..., description="The glob pattern to match files, e.g., '*.py',或者src/*.py,即如果指定路径就检索路径下的py文件，不然递归检索所有py文件")
    path:Path=Field(default_factory=lambda: Path('.'),description='要检索的文件夹路径')

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
                        hits.append(f'[{file_path}]: {line_index}: {line.rstrip()}')
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
    offset: Optional[int] = Field(None, ge=1, strict=True, description="Line number to start reading from (1-indexed)")
    limit: Optional[int] = Field(None, ge=1, strict=True, description="Maximum number of lines to read")

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

    if not editfileinput.old_string:
        raise ValueError('old_string must not be empty')
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
    start_line=content.index(old_string)
    start_line=content[:start_line].count('\n')+1
    old_n=len(_split_lines(old_string))
    new_n=len(_split_lines(new_string))
    hits=content.count(old_string)
    content= content.replace(old_string,new_string) if editfileinput.replace_all else content.replace(old_string,new_string,1)
    total_n=len(_split_lines(content))
    atomic_write(path=path,content=content)
    head=f'已编辑，旧内容{old_n}行，新内容{new_n}，一共{total_n}行'
    shown=_split_lines(new_string)[:cfg.diff_echo_lines]
    body='\n'.join(f'{start_line+index:>6}\t{line}' for index,line in enumerate(shown))
    if new_n>cfg.diff_echo_lines:
        body += f'\n[只显示新内容的前 {cfg.diff_echo_lines} 行]'
    return head+'\n'+body

   

# 6. run_bash
class RunBashInput(BaseModel):
    model_config = ConfigDict(extra = 'forbid')
    command: str = Field(..., description="The bash command to execute on the host terminal")
    
def run_bash(runbashinput: RunBashInput,cfg:config):
    from mini_harness.process import run_command
    code, output = run_command(runbashinput.command, cfg.work_space,
                               cfg.remaining(cfg.bash_timeout), cfg.bash_env, cfg.bash_limit)
    if code:
        raise RuntimeError(f'exit code: {code}\n{output}')
    return output or 'no result'

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
    if workspace.is_symlink() or not workspace.resolve().is_relative_to(root):
        raise PermissionError(f'{workspace}必须是{root}下的真实路径')
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
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
            '--ulimit', 'fsize=16777216:16777216', '--user', (f'{os.getuid()}:{os.getgid()}' if hasattr(os, 'getuid') else '1000:1000'),
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
        process.wait(timeout=cfg.remaining(runsandboxinput.timeout_seconds + 10))
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
    from mini_harness.tool.utils import TodoManger
    return TodoManger().update(runtodoinput.todos)
    


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
def run_subagent(runsubagentinput: RunSubAgentInput, cfg: config):
    from dataclasses import replace, asdict
    from mini_harness.agent import DeepSeekAgent
    from mini_harness.tool.box import TOOLS
    profile = SUBAGENT.agent_table[runsubagentinput.agent_type.value]
    child_cfg = replace(cfg, run_id='', user_name='subagent-' + uuid.uuid4().hex, child_results=[])
    agent = DeepSeekAgent([t for t in TOOLS if t.name in profile['tools']], child_cfg)
    agent.system_prompt[0]['content'] += '\n' + profile['prompt']
    result = agent.run_task(runsubagentinput.prompt)
    cfg.child_results.append(asdict(result))
    if result.outcome == 'timeout':
        raise TimeoutError(result.err)
    if result.outcome != 'completed':
        raise RuntimeError(f'Subagent {result.outcome}: {result.err}')
    return agent.messages[-1].get('content') or 'Completed without text'
