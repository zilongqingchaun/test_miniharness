from dataclasses import dataclass
import json
from logging import ERROR
import re

from pathlib import Path
from tkinter import E, EXCEPTION
from mini_harness.tool.tag import TAG
from pydantic import ValidationError
from mini_harness.tool.tools import write_file, edit_file
from mini_harness.tool.utils import digest, render_lines
from mini_harness.config import CONFIG

# write_tools=[write_file,edit_file]
write_tools = ['write_file', 'edit_file']


# 返回结果
@dataclass(frozen=True)
class ToolItem:
    content: str = ''
    ok: bool = True
    tag: str = ''


@dataclass
class record:
    mtime: float
    level: str
    edits: int
    hash: str = ''


class Level:
    PARTIAL: str = 'partial'
    FULL: str = 'full'


# 实现mark、fresh函数、gate和record，分别实现更新record状态，
# 检查文件是否被外界文件修改，gate判断是否应该修改文件的逻辑，
# 最后execute_tool里面负责调用上面几个函数
# 负责管理工具,暂时不实现其他安全性逻辑判断和具体工具实现
class ToolManager:
    def __init__(self, registry: dict, cfg=CONFIG):
        self.registry = registry
        self.files = {}
        self.last_tool = {}
        self.cfg = cfg

    # 更新文件状态
    def mark(self, file_path: str, level: str):
        p = Path(file_path).resolve()

        try:
            mtime = p.stat().st_mtime
            hash_value = digest(p)
        except OSError:
            return

        old = self.files.get(p)

        if level == Level.FULL or (
            old is not None and old.level == Level.FULL
        ):
            level = Level.FULL
        else:
            level = Level.PARTIAL

        edits = old.edits if old is not None else 0

        self.files[p] = record(
            mtime=mtime,
            level=level,
            edits=edits,
            hash=hash_value
        )

    def fresh(self, file_path: Path | str):
        # 没有变化返回True
        p = Path(file_path).resolve()

        if p.is_file():
            file = self.files.get(p)
            if file is not None:
                if file.mtime == p.stat().st_mtime:
                    return True
                if file.hash == digest(p):
                    return True

        return False

    def deny(self, file_path, msg: str, tag):
        try:
            body, reach_end = render_lines(
                Path(file_path), None, None
            )
            self.mark(
                file_path=file_path,
                level=Level.FULL if reach_end else Level.PARTIAL
            )
            return ToolItem(
                f'[{tag}]: {msg}\n\nCurrent content of {file_path}:\n{body}',
                False,
                tag
            )
        except Exception as e:
            return ToolItem(
                f'[{tag}]: {msg}\n\n'
                f'[Counld not display the file: {type(e).__name__}. '
                f'Read it with read_file before before retrying]',
                False,
                tag
            )

    # 传入需要args需要传回需要读取的文件
    def gate(self, func_name: str, params) -> ToolItem | None:
        # 需要传入函数名，才能判断需要调用的函数是读还是写
        # 之前是否读取过
        if func_name not in write_tools:
            return None

        file_path = Path(params.path).resolve()
        exists = file_path.is_file()

        # 文件不存在时，交给具体工具函数处理
        if not exists:
            return None

        # edit需要判断文件是否被更改过，以及是否被读取过
        # write需要判断是否允许重置，是否完整读取过，以及是否被更改过
        if func_name == 'edit_file':
            if file_path not in self.files:
                return self.deny(
                    file_path=file_path,
                    msg='文件从未读取过，请读取后再来尝试',
                    tag=TAG.NEED_READ
                )

            if not self.fresh(file_path):
                return self.deny(
                    file_path=file_path,
                    msg='文件已经被更改，请尝试重新读取一遍文件',
                    tag=TAG.STALE
                )

        if func_name == 'write_file':
            if not params.overwrite:
                return self.deny(
                    file_path=file_path,
                    msg='未批准覆盖',
                    tag=TAG.EXISTS
                )

            file = self.files.get(file_path)

            if file is None or file.level != Level.FULL:
                return self.deny(
                    file_path=file_path,
                    msg='覆盖文件前请完整阅读过整个文件',
                    tag=TAG.NEED_FULL
                )

            if not self.fresh(file_path):
                return self.deny(
                    file_path=file_path,
                    msg='文件已经被更改，请尝试重新读取一遍文件',
                    tag=TAG.STALE
                )

        return None

    def Record(self, func: str, args, content):
        if func == 'read_file':
            self.mark(
                file_path=args.path,
                level=Level.PARTIAL if '[Showing lines ' in content else Level.FULL
            )

        elif func == 'grep_file':
            for hit in set(
                re.compile(r'^\[(.+?)\]: \d+: ', re.M).findall(content)
            ):
                self.mark(hit, Level.PARTIAL)

        elif func in write_tools:
            file_path = Path(args.path).resolve()

            old = self.files.get(file_path)
            edits = old.edits if old else 0
            level = Level.FULL if func == 'write_file' else (
                old.level if old else Level.FULL
            )

            self.mark(file_path, level)

            if file_path in self.files:
                self.files[file_path].edits = edits + 1

                n = self.files[file_path].edits
                if self.cfg.thrash_notice and n >= self.cfg.thrash_notice:
                    content += (
                        f'\n\n[You have modified this file {n} times without the task passing].\n'
                        f'Consider re-reading it in full, or reconsidering the approach'
                    )

        return content

    # 传入call
    def execute_tool(self, tool_call):
        name = tool_call.function.name
        definition = self.registry.get(name)

        # 得到ToolDefinition对象
        # 未知工具
        if definition is None:
            return ToolItem(
                content=f'unknown tool: {name}',
                ok=False,
                tag=TAG.UNKNOWN_TOOL
            )

        try:
            args = json.loads(tool_call.function.arguments or '{}')
            params = definition.parameters(**args)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            # 参数非法
            return ToolItem(
                content=f'{type(e).__name__}:{e}',
                ok=False,
                tag=TAG.INVALID_ARGS
            )

        # 通过gate才能判断是否执行后续程序
        # 返回None则不管，能否执行由后续判断，返回其他则直接返回
        gate_result = self.gate(name, params)

        if gate_result is not None:
            return gate_result

        try:
            result = definition.function(params,self.cfg)
        except Exception as e:
            return ToolItem(
                content=f'{type(e).__name__}:{e}',
                ok=False,
                tag=TAG.EXECUTE_FAILED
            )

        raw = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False
        )

        raw = self.Record(name, args=params, content=raw)

        return ToolItem(content=raw, ok=True, tag=TAG.SUCCESS)