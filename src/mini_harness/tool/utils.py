from pathlib import Path
import hashlib
from types import _ReturnT_co
from mini_harness.config import CONFIG,config
import fnmatch
import os
import re
#tool-manager

def digest(p: Path) -> str:
    h = hashlib.md5()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()

def render_lines(file_path: Path, offset: int|None, limit: int|None, cfg = CONFIG) -> tuple[str, bool]:
    try:
        content = file_path.read_text(encoding = 'utf-8')
    except UnicodeDecodeError:
        size = file_path.stat().st_size
        return f'[Binary or non-UTF-8 file, {size} bytes. Content not displayed]', True
    lines = _split_lines(content)
    total = len(lines)
    if total == 0:
        return f'[The file is empty (0 lines)]', True

    start = (offset if offset else 1) - 1
    end = start + limit if limit else total
    window = lines[start: end]
    if not window:
        raise ValueError(f'[offset error]: offset {offset} exceeds the file length ({total} lines), use an offset between 1 and {total}')

    out, used = [], 0
    for lineno, line in enumerate(window, start + 1): 
        render = f'{lineno:>6}\t{line}'
        out.append(render)
        used += len(render)
        if used >= cfg.read_limit:
            break

    last = start + len(out)
    body = '\n'.join(out)
    reach_end = (last >= total)
    if not reach_end:
        body += f'\n\n{start+1}-{last} of {total}, use offset {last + 1} to continue]'

    return body, reach_end


def _split_lines(content: str) -> list[str]:
    if not content:
        return []

    content = content.replace('\r\n', '\n')
    content_split = content.split('\n')
    if content_split and not content_split[-1]:
        content_split.pop()
    return content_split

def atomic_write(path:Path,content:str):
    path.parent.mkdir(parents = True, exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    try:
        temp.write_text(content,encoding='utf-8')
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)

#tool
#判断文件夹和文件是否允许读取 返回true则不可读取
def is_denied(file_path:Path,cfg:config):
    if any(part in cfg.deny_dir  for part in file_path.parts):
        return True
    return any(fnmatch.fnmatch(file_path.name,part) for part in cfg.deny_name)

LINE_NO = re.compile(r'^\s*\d+\t')
def strip_line_no(content:str):
    lines=_split_lines(content=content)
    #判空
    if not lines:
        return content
    if all(LINE_NO.match(line) for line in lines):
        return '\n'.join(LINE_NO.sub('',line) for line in lines)
    return content


class TodoManger:
    def __init__(self) -> None:
        self.items = []
        return

    def update(self, items: list) -> str:
        self.items = items
        return self.render()

    def render(self) -> str:
        if not self.items:
            return 'no items'
        action = []
        for item in self.items:
            if item.status == 'completed':
                action.append(f'[x] {item.content}')
            elif item.status == 'in_progress':
                action.append(f'[>] {item.activeForm}')
            else:
                action.append(f'[ ] {item.content}')
        completed = sum(1 for t in self.items if t.status == 'completed')
        action.append(f'{completed} / {len(self.items)} completed')
        return '\n'.join(action)


class SubAgent:
    def __init__(self) -> None:
        self.agent_table = {
            'explore_agent': {
                'description': 'explore, read and find the file and file content based on the task',
                'tools': ['read_file', 'glob_file', 'grep_file'],
                'prompt':  """
You are a explore-agent, your job is to read and analyze the file. Here is your chain of thought:
step1: scan the file: Use the tool "read_file", "grep_file" to scan and check the file content which you are interested
step2: Analyze the file: try to analyze the situation of the task and read the whole content which you want to read by using the tool "read_file"
"""
            },
            'coding_agent': {
                'description': 'create, code and program the file based on the task',
                'tools': ['read_file', 'glob_file', 'grep_file', 'write_file', 'edit_file'],
                'prompt': """
You are a coding-agent, your job is to code and write the file. Here is you chain of thought:
step1: thinking about the task(question): Use the tool "read_file", "glob_file", "grep_file" to know about the situation real_time
step2: design multiple way of solution: try to thinking about some strategy which can solve the question
step3: pick the besk solution and start writing: pick the best solution to write the content, use the tool like, "write_file", "edit_file" to write the content
"""
            },
            'planning_agent': {
                'description': 'read, analyze and make a plan to better finish the task',
                'tools': ['read_file', 'glob_file', 'grep_file', 'write_file', 'edit_file'],
                'prompt': """
You are a planing_agent, your jon is to make plan of the task. Here is your chain of thought:
step1: thinking about the taks: try to analyzing the goal of user, and use the tool "read_file", 'glob_file", "grep_file" to know about the situation,
step2: write the plan: after analyzing, use the tool "write_file", "edit_file" to write a md file to show the plan and give the user your analzing report and the path of md file
"""
            }
            
        }
def to_api_tool(tools: list) -> list[dict]:
    return [
            {
                'type': 'function',
                'function': {
                    'name': t.name,
                    'description': t.description,
                    'parameters': t.parameters.model_json_schema()
                }
            } for t in tools
        ]



TODO=TodoManger()
SUBAGENT=SubAgent()