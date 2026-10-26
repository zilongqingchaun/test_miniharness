from asyncio import taskgroups
from dataclasses import dataclass
from json import tool
import os
from urllib import response
from mini_harness.prompts.system_prompt import system_prompt
from pathlib import Path
from mini_harness.config import CONFIG
import os
import json
from openai import OpenAI,LengthFinishReasonError
from mini_harness.tool.tool_manager import ToolManager
from mini_harness.tool.tag import OUTCOME, Outcome
import time
import random

from openai import APIConnectionError, InternalServerError, RateLimitError
from typing import Callable
from httpx import RemoteProtocolError
from mini_harness.tool.tag import TAG



#各个字段的含义
#out_come:最终输出的状态 calls:工具调用总次数 turns:llm请求轮数 ok：成功的调用次数
#failed_by_tag按错误类型统计次数 calls_by_tool按照工具名称统计调用次数 last_prompt：最近一次记录的 LLM 请求输入 Token 数
#prompt_total 本次任务累计输入token completion_total：累计输出token wall：耗时 err错误信息
#prompt_total主要是messages和执行结果，token completion_total是输出token，包括文件编辑和思考链等
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


#需要完成的几个功能，加载历史记录、压缩历史记录、保存历史记录、调用工具、流式打印输出
class DeepSeekAgent:
    def __init__(self,tools:list,cfg=CONFIG):
        self.tools=self._to_api_tool(tools)
        self.system_prompt=[{'role':'system','content':system_prompt}]
        self.messages=list(self.system_prompt)
        self.history_session:str=cfg.history_session_path
        self.user_id:str=cfg.user_name
        self.cfg=cfg
        self.tool_registry={t.name:t for t in tools}
        self.printed=''
        self.last_usage=None
        self.last_reasoning=''

    #定义关于历史记录保存和加载的函数
    def load_memory(self):
        os.makedirs(self.history_session,exist_ok=True)
        history_file_path=os.path.join(self.history_session,f'{self.user_id}.json')
        #如果不存在则新建
        file_path=os.path.isfile(history_file_path)
        if not file_path:
            with open(history_file_path,mode='w',encoding='utf-8',) as f:
                json.dump({},f,ensure_ascii=False,indent=2)
        #存在则读取
        self.messages=json.loads(Path(history_file_path).read_text(encoding='utf-8'))
        
    def save_memory(self):
        os.makedirs(self.history_session,exist_ok=True)
        history_file_path=Path(os.path.join(self.history_session,f'{self.user_id}.json'))
        #将历史对话保存
        history=json.dumps(self.messages,ensure_ascii=False,indent=2)
        temp=history_file_path.with_name(history_file_path.name+'.tmp')
        try:
            temp.write_text(history,encoding='utf-8')
            os.replace(temp,history_file_path)
        finally:
            temp.unlink(missing_ok=True)
    #llm根据历史对话进行判断和准备使用工具，返回response
    def request_agent(self,client:OpenAI,cfg=None):
        buffer=''
        reasoning=''
        streaming=False
        #是否截断
        truncated=None
        with client.chat.completions.stream(
        model=cfg.model_name,                      # 补上模型名
        tools=self.tools,
        messages=self.request_messages(self.messages),
        stream_options={'include_usage': True},
        temperature=0
        ) as e:
            for d in e:
                #流式输出文字内容
                if d.type=='content.delta':
                    #正常情况下
                    buffer=buffer+d.delta
                    
                    if streaming:
                        print(d.delta,end='',flush=True)
                        self.printed += d.delta
                        continue
                    length_buffer=len(buffer)
                    length_printed=len(self.printed)
                    n=min(length_printed,length_buffer)
                    if buffer[:n]!=self.printed[:n]:
                        #链接断开，这是重新生成了一份回答，重置
                        print(buffer, end = '', flush = True)
                        self.printed = buffer
                        streaming = True
                    elif length_buffer>length_printed:
                        #输出长度不一致,补齐之后的内容
                        print(buffer[length_printed:],end='',flush=True)
                        self.printed=buffer
                        streaming=True
                if d.type=='chunk':
                    #取出token用量，包括输入+输出所有token
                    # d.chunk.usage.completion_tokens
                    # d.chunk.usage.prompt_tokens
                    u=getattr(d.chunk,'usage',None)
                    if u is not None:
                        self.last_usage=u
                    #openai不支持ds的思维链，需要自己捕获
                    if d.chunk.choices:
                        res=getattr(d.chunk.choices[0].delta,'reasoning_content','')
                        if res:
                            reasoning+=res
                            print(res,end='',flush=True)
            try:
                #流式输出结束，读取全部response
                response=e.get_final_completion()
                #opai不支持ds的思维链字段，手动指定
                response.choices[0].message.reasoning_content=reasoning

            except LengthFinishReasonError as e:
                #输出超过上限，统计token用量，response不可信,返回截断输出
                truncated=buffer
                #提取usage
                comp=getattr(e,'completion',None)
                if comp:
                    u=getattr(comp,'usage',None)
                    self.last_usage=u
                else:
                    u=None
                response=None
        #断线恢复，本次无内容输出或者输出内容小于printed
        if not streaming and self.printed:
            print(f'\n--- [reconnection lost, the context above is useless] ----')
            if buffer:
                print(buffer,end='',flush=True)
            self.printed=buffer
        self.last_reasoning=reasoning
        return response,truncated







    #在规定次数内进行迭代，如果没有工具调用或者超时则结束，有工具调用则执行工具并返回结果 以及实现流式输出
    def run_turn(self,client,tool_manager):
        #初始化result所需字段
        outcome = OUTCOME.ERROR
        turns = calls = ok = last_prompt = prompt_total = completion_total = wall = 0
        err = ''
        start=0
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
            
            start=time.time()
            for turn in range(self.cfg.max_turn):
                #检测超时outcome，for之前启动运行时间，for内部判断是否超过cfg的时间限制。
                
                if self.cfg.max_run_time and time.time()-start>=self.cfg.max_run_time:
                    outcome=Outcome.TIMEOUT
                    print('时间耗尽')
                    break
                
                turns=turns+1
                self.printed=''
                self.last_usage=None
                self.last_reasoning=''


                response,truncated=retry_call(lambda:self.request_agent(client,self.cfg),cfg=self.cfg)
                #截断统计逻辑
                if truncated is not None:
                    #response为None，需要自己构造本次对话，并统计可能的token消耗
                    note='本次回复输出内容过长，已经截断'
                    self.messages.append({'role':'assistant','content':truncated+note,'reasoning_content':self.last_reasoning})
                    if self.last_usage:
                        last_prompt=getattr(self.last_usage,'prompt_tokens',0)
                        prompt_total+=last_prompt
                        completion_total+=getattr(self.last_usage,'completion_tokens',0)
                    continue

                #正常统计
                last_prompt=getattr(self.last_usage,'prompt_tokens',0)
                prompt_total+=last_prompt
                completion_total+=getattr(self.last_usage,'completion_tokens',0)

                #判断是否需要执行工具
                message=response.choices[0].message

                #加入历史记录
                self.messages.append(message.model_dump(exclude_none=True))
                #得到需要执行的工具
                tool_calls=message.tool_calls
                if tool_calls:
                    #执行工具的逻辑
                    for tool_call in tool_calls:
                        #工具调用次数加1
                        calls+=1

                        tool_call_id=tool_call.id
                        tool_result=tool_manager.execute_tool(tool_call)
                        self.messages.append({'role':'tool','tool_call_id':tool_call_id,'content':tool_result.content})
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
                    #llm认为不需要调用工具栏了，react范式里一般是任务执行完了
                    print('执行完毕')
                    outcome=OUTCOME.COMPLETED
                    self.save_memory()
                    break
            else:
                #次数用完了
                print('次数耗尽')
                outcome=OUTCOME.EXHAUSTED
                self.save_memory()
                

        except Exception as e:
            outcome = OUTCOME.ERROR
            err = f'{type(e).__name__}:{e}'
            print(f'[run failed]: agent run failed: {err}')
            self.save_memory()

        wall = time.time() - start
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
             if key not in {'reasoning_content', 'annotations', 'parsed'}}
            for message in messages
        ]




    #根据传入task执行任务，无上下文，所以不用载入上下文,需要重置message防止不同任务混淆
    def run_task(self,task:str):
        self.messages=list(self.system_prompt)
        self.messages.append({'role':'user','content':task})
        #配置llm客户端和工具调度器
        client=OpenAI(api_key=self.cfg.model_api_key,base_url=self.cfg.base_url)
        tool_manager=ToolManager(registry=self.tool_registry,cfg=self.cfg)
        #传入客户端和工具调度器进行迭代 保存结果可选
        result=self.run_turn(client=client,tool_manager=tool_manager)
        #返回结果，便于后续指标计算 result类型为Result
        return result
        
    
def retry_call(request_agent: Callable, cfg = CONFIG):
    for attempt in range(max(cfg.max_retry, cfg.rate_retry)):
        try:
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
            time.sleep(wait)
    raise ValueError(f'[invalid retries]: the max retry is invalid: {cfg.max_retry}')



        
# ds=DeepSeekAgent(tools=["query","edit"])
# ds.load_memory()