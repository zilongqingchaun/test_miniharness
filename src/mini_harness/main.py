import argparse
from ast import Pass, arg
import sys
from mini_harness.tool.tag import OUTCOME
from mini_harness.config import CONFIG
from mini_harness.tool.box import TOOLS
from mini_harness.agent import DeepSeekAgent


#主函数-->agent-->工具调度
#定义输出状态：完成、错误、耗尽、超时、中断
EXIT = {
    OUTCOME.COMPLETED: 0,
    OUTCOME.ERROR: 1,
    OUTCOME.EXHAUSTED: 3,
    OUTCOME.TIMEOUT: 4,
    OUTCOME.INTERRUPTED: 130
}

def main(cfg=CONFIG):
    #配置参数解析器
    parser=argparse.ArgumentParser()
    parser.add_argument('--task',default=None,help='the todo task')
    # parser.add_argument('--telemetry-out',default=None,help='the json path')#保存路径
    arg=parser.parse_args()
    if cfg.agent_task_mode and arg.task is None:
        parser.error('--task is required when agent_task_mode is enabled')
    #为agent传入工具和参数。
    agent=DeepSeekAgent(TOOLS,cfg=CONFIG)
    #两种运行模式，一种不传入任务，跟着用户的对话进行，一种传入任务
    #执行任务模式，首先开发task模式
    if not cfg.agent_task_mode:
        # agent.run()
        return
    #task模式用来完成评估
    result=agent.run_task(arg.task)
    #写入结果
    print(result)
    sys.exit(EXIT[result.outcome])



if __name__=='__main__':
    main()
