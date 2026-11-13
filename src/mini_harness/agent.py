import json
from dataclasses import dataclass, asdict, replace
from pathlib import Path

from mini_harness.config import CONFIG
from mini_harness.prompts.system_prompt import system_prompt
from openai import OpenAI,LengthFinishReasonError
from mini_harness.tool.tool_manager import ToolManager
from mini_harness.tool.tag import OUTCOME, Outcome
import time
import random
import uuid

from openai import APIConnectionError, InternalServerError, RateLimitError
from typing import Callable
from httpx import RemoteProtocolError
from mini_harness.tool.tag import TAG




@dataclass(frozen = True)
class Result:
    outcome: str
    calls: int
    turns: int
    ok: int
    failed_by_tag: dict
    calls_by_tool: dict
    last_prompt: int
    prompt_total: int
    completion_total: int
    wall: float
    err: str = ''



class DeepSeekAgent:
    def __init__(self,tools:list,cfg=CONFIG):
        self.tools=self._to_api_tool(tools)
        self.system_prompt=[{'role':'system','content':system_prompt.replace('./sandbox', str(cfg.sand_box)).replace('Write to "sandbox/xxx.py", not "xxx.py".', f'Write within {cfg.sand_box}.') }]
        self.messages=list(self.system_prompt)
        self.history_session:str=cfg.history_session_path
        self.user_id:str=cfg.user_name
        self.cfg=replace(cfg)
        self.trajectory=[]
        self.system_prompt[0]['content'] += f'\nRuntime tool policy: {cfg.risky_policy}. This policy overrides earlier approval instructions.'
        self.tool_registry={t.name:t for t in tools}
        self.printed=''
        self.last_usage=None
        self.last_reasoning=''


    def load_memory(self):
        path = Path(self.history_session) / f'{self.user_id}.json'
        if not path.exists():
            self.messages = list(self.system_prompt)
            return
        messages = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(messages, list) or any(not isinstance(m, dict) or 'role' not in m for m in messages):
            raise ValueError('History must be a list of messages')
        self.messages = messages

    def save_memory(self):
        from mini_harness.tool.utils import atomic_write
        root = Path(self.history_session)
        atomic_write(root / f'{self.user_id}.json', json.dumps(self.messages, ensure_ascii=False, indent=2))
        atomic_write(root / f'{self.user_id}.trajectory.json', json.dumps(self.trajectory, ensure_ascii=False, indent=2))

    def compact(self):
        # Keep complete assistant/tool groups; the full transcript remains in trajectory.
        if len(json.dumps(self.messages, ensure_ascii=False)) <= self.cfg.context_chars:
            return
        cut = max(2, len(self.messages) - self.cfg.recent_keep)
        while cut > 2 and self.messages[cut].get('role') == 'tool':
            cut -= 1
        if cut <= 2:
            raise ValueError('Current message group exceeds context budget')
        old = self.messages[2:cut]
        notes = []
        for m in old:
            text = m.get('content') or ''
            if m.get('tool_calls'):
                text += json.dumps(m['tool_calls'], ensure_ascii=False)
            notes.append(m['role'] + ': ' + text[:300])
        summary = '\n'.join(notes)[-12000:]
        self.messages = self.messages[:2] + [{'role': 'user', 'content':
            'Earlier context excerpts (possibly incomplete; re-read files as needed):\n' + summary}] + self.messages[cut:]
        if len(json.dumps(self.messages, ensure_ascii=False)) > self.cfg.context_chars:
            raise ValueError('Recent context exceeds budget; reduce tool output limits')

    def dump_run(self, result, task, path):
        from mini_harness.tool.utils import atomic_write
        data = dict(asdict(result), task=task, run_id=self.user_id, model=self.cfg.model_name,
                    workspace=str(self.cfg.work_space), child_runs=self.cfg.child_results)
        atomic_write(Path(path), json.dumps(data, ensure_ascii=False, indent=2))

    def request_agent(self, client: OpenAI, cfg=None):
        cfg = cfg or self.cfg
        buffer, reasoning = '', ''
        try:
            with client.chat.completions.stream(
                **cfg.request_options(), tools=self.tools or None,
                messages=self.request_messages(self.messages),
                stream_options={'include_usage': True},
            ) as stream:
                for event in stream:
                    cfg.remaining()
                    if event.type == 'content.delta':
                        buffer += event.delta
                        # Avoid replaying the identical prefix after a connection retry.
                        if buffer.startswith(self.printed):
                            print(buffer[len(self.printed):], end='', flush=True)
                        elif not self.printed.startswith(buffer):
                            print('\n[retried response] ' + buffer, end='', flush=True)
                        self.printed = buffer
                    elif event.type == 'chunk':
                        if event.chunk.usage is not None:
                            self.last_usage = event.chunk.usage
                        if event.chunk.choices:
                            reasoning += getattr(event.chunk.choices[0].delta, 'reasoning_content', '') or ''
                response = stream.get_final_completion()
                self.last_usage = response.usage or self.last_usage
                if reasoning:
                    response.choices[0].message.reasoning_content = reasoning
                self.last_reasoning = reasoning
                return response, None
        except LengthFinishReasonError as error:
            completion = getattr(error, 'completion', None)
            if completion:
                self.last_usage = completion.usage or self.last_usage
            self.last_reasoning = reasoning
            return None, buffer


    def run_turn(self,client,tool_manager):
        #初始化result所需字段
        outcome = OUTCOME.ERROR
        turns = calls = ok = last_prompt = prompt_total = completion_total = wall = 0
        err = ''
        start=time.monotonic()
        child_start = len(self.cfg.child_results)
        failed_by_tag = {
                    TAG.INVALID_ARGS: 0,
                    TAG.DENIED: 0,
                    TAG.EXECUTE_FAILED: 0,
                    TAG.DEDUP: 0,
                    TAG.UNKNOWN_TOOL: 0,
                    TAG.NEED_READ: 0,
                    TAG.STALE: 0,
                    TAG.EXISTS: 0,
                    TAG.NEED_FULL: 0
                }
        calls_by_tool = {
                    'glob_file': 0,
                    'grep_file': 0,
                    'read_file': 0,
                    'write_file': 0,
                    'edit_file': 0,
                    'run_bash': 0,
                    'run_sandbox': 0,
                    'run_todo': 0,
                    'run_subagent': 0
                }
        
        try:
            
            start=time.monotonic()
            if self.cfg.deadline is None and self.cfg.max_run_time:
                self.cfg.deadline = start + self.cfg.max_run_time
            child_start = len(self.cfg.child_results)
            tool_manager.last_tool = None
            for turn in range(self.cfg.max_turn):

                
                if self.cfg.max_run_time and time.monotonic()-start>=self.cfg.max_run_time:
                    outcome=Outcome.TIMEOUT
                    print('时间耗尽')
                    break
                
                turns=turns+1
                self.printed=''
                self.last_usage=None
                self.last_reasoning=''


                self.cfg.remaining()
                self.compact()
                response,truncated=retry_call(lambda:self.request_agent(client,self.cfg),cfg=self.cfg)

                if truncated is not None:

                    note='本次回复输出内容过长，已经截断'
                    self.messages.append({'role':'assistant','content':truncated+note,'reasoning_content':self.last_reasoning})
                    self.trajectory.append(self.messages[-1])
                    if self.last_usage:
                        last_prompt=getattr(self.last_usage,'prompt_tokens',0)
                        prompt_total+=last_prompt
                        completion_total+=getattr(self.last_usage,'completion_tokens',0)
                    continue


                last_prompt=getattr(self.last_usage,'prompt_tokens',0)
                prompt_total+=last_prompt
                completion_total+=getattr(self.last_usage,'completion_tokens',0)

  
                message=response.choices[0].message

                self.messages.append(message.model_dump(exclude_none=True))
                self.trajectory.append(self.messages[-1])

                tool_calls=message.tool_calls
                if tool_calls:
                    
                    for tool_call in tool_calls:
                
                        calls+=1

                        tool_call_id=tool_call.id
                        tool_result=tool_manager.execute_tool(tool_call)
                        self.messages.append({'role':'tool','tool_call_id':tool_call_id,'content':tool_result.content})
                        self.trajectory.append(dict(self.messages[-1], ok=tool_result.ok, tag=tool_result.tag))
                        #是否调用成功以及对应的具体细分错误
                        is_ok=getattr(tool_result,'ok',None)
                        if is_ok:
                            ok+=1
                        else:
                            tool_tag=getattr(tool_result,'tag')
                            failed_by_tag[tool_tag]=failed_by_tag.get(tool_tag,0)+1
                        #按照tool统计
                        tool_name=tool_call.function.name
                        calls_by_tool[tool_name]=calls_by_tool.get(tool_name,0)+1
                else:

                    print('执行完毕')
                    outcome=OUTCOME.COMPLETED

                    break
            else:
                #次数用完了
                print('次数耗尽')
                outcome=OUTCOME.EXHAUSTED

                

        except KeyboardInterrupt:
            outcome = OUTCOME.INTERRUPTED
            err = 'Interrupted by user'
        except TimeoutError as e:
            outcome = OUTCOME.TIMEOUT
            err = str(e)
        except Exception as e:
            outcome = OUTCOME.ERROR
            err = f'{type(e).__name__}:{e}'
            print(f'[run failed]: agent run failed: {err}')

        # Complete pending call groups so a saved session can be resumed safely.
        pending = {}
        for m in self.messages:
            for call in m.get('tool_calls', []):
                pending[call['id']] = True
            if m.get('role') == 'tool':
                pending.pop(m['tool_call_id'], None)
        for call_id in pending:
            self.messages.append({'role': 'tool', 'tool_call_id': call_id,
                                  'content': 'Execution interrupted; result unavailable'})
        try:
            self.save_memory()
        except OSError as e:
            outcome, err = OUTCOME.ERROR, f'Could not persist run: {e}'
        for child in self.cfg.child_results[child_start:]:
            prompt_total += child['prompt_total']
            completion_total += child['completion_total']
        wall = time.monotonic() - start
        return Result(
            outcome=outcome,
            turns=turns,
            calls=calls,
            ok=ok,
            prompt_total=prompt_total,
            completion_total=completion_total,
            err=err,
            failed_by_tag=failed_by_tag,
            calls_by_tool=calls_by_tool,
            last_prompt=last_prompt,
            wall=wall
        )
    #封装工具
    def _to_api_tool(self,tools: list) -> list[dict]:
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


    def request_messages(self, messages: list) -> list:
        return [
            {key: value for key, value in message.items()
             if key not in ({'annotations', 'parsed'} if self.cfg.preserve_reasoning else {'reasoning_content', 'annotations', 'parsed'})}
            for message in messages
        ]




    #根据传入task执行任务，无上下文，所以不用载入上下文,需要重置message防止不同任务混淆
    def run_task(self,task:str):
        self.user_id = self.cfg.run_id or uuid.uuid4().hex
        self.messages = list(self.system_prompt) + [{'role': 'user', 'content': task}]
        self.trajectory = list(self.messages)
        with OpenAI(api_key=self.cfg.model_api_key or 'missing', base_url=self.cfg.base_url,
                    max_retries=0, timeout=self.cfg.api_timeout) as client:
            inherited_deadline = self.cfg.deadline
            try:
                result = self.run_turn(client, ToolManager(self.tool_registry, self.cfg))
            finally:
                self.cfg.deadline = inherited_deadline
        self.dump_run(result, task, Path(self.history_session) / f'{self.user_id}.result.json')
        return result


def retry_call(request_agent: Callable, cfg = CONFIG):
    for attempt in range(max(cfg.max_retry, cfg.rate_retry)):
        try:
            cfg.remaining()
            return request_agent()
        except (APIConnectionError, InternalServerError, RateLimitError, RemoteProtocolError) as e:
            limited = isinstance(e, RateLimitError)
            allow = cfg.rate_retry if limited else cfg.max_retry
            if attempt >= allow - 1:
                raise
            base = cfg.rate_base if limited else cfg.retry_base
            wait = base * (2 ** attempt) + random.uniform(0, 1)
            if limited:
                wait = min(wait, cfg.rate_cap)
            print(f'[retry]: the api connection lost: {type(e).__name__}, waiting for retry -- {attempt +1 }/{cfg.max_retry} -- {wait:.1f}s')
            time.sleep(min(wait, cfg.remaining()))
            cfg.remaining()
    raise ValueError(f'[invalid retries]: the max retry is invalid: {cfg.max_retry}')



        
# ds=DeepSeekAgent(tools=["query","edit"])
# ds.load_memory()