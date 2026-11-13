from dataclasses import dataclass
import json
from logging import ERROR
import re

from pathlib import Path
from mini_harness.tool.tag import TAG
from pydantic import ValidationError
from mini_harness.tool.utils import digest, render_lines, is_denied
from mini_harness.config import CONFIG


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



class ToolManager:
    def __init__(self, registry: dict, cfg=CONFIG):
        self.registry = registry
        self.files = {}
        self.last_tool = None
        self.cfg = cfg

    def resolve(self, path):
        p = Path(path)
        return (p if p.is_absolute() else self.cfg.work_space / p).resolve()

    # 更新文件状态
    def mark(self, file_path: str, level: str):
        p = self.resolve(file_path)

        try:
            mtime = p.stat().st_mtime
            hash_value = digest(p)
        except OSError:
            return

        old = self.files.get(p)

        if level == Level.FULL or (
            old is not None and old.level == Level.FULL and old.hash == hash_value
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
        p = self.resolve(file_path)

        if p.is_file():
            file = self.files.get(p)
            if file is not None:
                if file.hash == digest(p):
                    return True

        return False

    def deny(self, file_path, msg: str, tag):

        try:
            body, reach_end = render_lines(Path(file_path), None, None, self.cfg)
            self.mark(file_path, Level.FULL if reach_end else Level.PARTIAL)
            return ToolItem(
                content=f'[{tag}]: {msg}\n\nCurrent content of {file_path}:\n{body}',
                ok=False,
                tag=tag
            )
        except Exception:
            return ToolItem(f'[{tag}]: {msg}; read_file explicitly before retrying', False, tag)


    def gate(self, func_name: str, params) -> ToolItem | None:


        if func_name not in write_tools:
            return None

        file_path = self.resolve(params.path)
        exists = file_path.is_file()


        if not exists:
            return None


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
                level=Level.PARTIAL if ('[Showing lines ' in content or 'Content not displayed' in content or '[truncated' in content) else Level.FULL
            )

        elif func == 'grep_file':
            for hit in set(
                re.compile(r'^\[(.+?)\]: \d+: ', re.M).findall(content)
            ):
                self.mark(hit, Level.PARTIAL)

        elif func in write_tools:
            file_path = self.resolve(args.path)

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


        if definition is None:
            self.last_tool = None
            return ToolItem(
                content=f'unknown tool: {name}',
                ok=False,
                tag=TAG.UNKNOWN_TOOL
            )

        try:
            args = json.loads(tool_call.function.arguments or '{}')
            params = definition.parameters(**args)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:

            self.last_tool = None
            return ToolItem(
                content=f'{type(e).__name__}:{e}',
                ok=False,
                tag=TAG.INVALID_ARGS
            )

        current = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
        if self.last_tool == current:
            return ToolItem(
                content=f'[{TAG.DEDUP}]: identical tool call repeated; change the tool or the arguments',
                ok=False,
                tag=TAG.DEDUP
            )
        self.last_tool = None

        try:
            self.cfg.remaining()
            if hasattr(params, 'path'):
                path = self.resolve(params.path)
                guard = self.cfg.guard_write if name in write_tools else self.cfg.guard_read
                root = self.cfg.sand_box if name in write_tools else self.cfg.work_space
                if guard and (not path.is_relative_to(root) or is_denied(path, self.cfg)):
                    raise PermissionError('Path denied')
                params.path = str(path) if isinstance(params.path, str) else path
            if definition.risky:
                allowed = self.cfg.risky_policy == 'allow'
                if self.cfg.risky_policy == 'ask' and callable(self.cfg.approval):
                    allowed = self.cfg.approval(name, args)
                if not allowed:
                    raise PermissionError('Risky tool requires explicit approval')
            gate_result = self.gate(name, params)
        except PermissionError as e:
            return ToolItem(str(e), False, TAG.DENIED)

        if gate_result is not None:
            return gate_result

        try:
            result = definition.function(params,self.cfg)
        except TimeoutError as e:

            self.cfg.remaining()
            return ToolItem(
                content=f'[{TAG.EXECUTE_FAILED}]: {e}',
                ok=False,
                tag=TAG.EXECUTE_FAILED
            )
        except Exception as e:
            return ToolItem(
                content=f'{type(e).__name__}:{e}',
                ok=False,
                tag=TAG.EXECUTE_FAILED
            )

        raw = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False
        )

        if len(raw) > self.cfg.clip_limit:
            raw = raw[:self.cfg.clip_limit] + '\n[truncated output]'
        raw = self.Record(name, args=params, content=raw)
        self.last_tool = current

        return ToolItem(content=raw, ok=True, tag=TAG.SUCCESS)