import argparse
from dataclasses import replace
from pathlib import Path
from openai import OpenAI
from mini_harness.config import CONFIG
from mini_harness.agent import DeepSeekAgent
from mini_harness.tool.box import TOOLS
from mini_harness.tool.tool_manager import ToolManager

EXIT = {'completed': 0, 'error': 1, 'exhausted': 3, 'timeout': 4, 'interrupted': 130}


def main(cfg=CONFIG):
    parser = argparse.ArgumentParser()
    parser.add_argument('--task')
    parser.add_argument('--workspace', type=Path, default=cfg.work_space)
    parser.add_argument('--telemetry-out', type=Path)
    parser.add_argument('--history-dir', default=cfg.history_session_path)
    parser.add_argument('--session', default='interactive')
    parser.add_argument('--run-id', help='Artifact basename for an isolated task run')
    parser.add_argument('--write-workspace', action='store_true',
                        help='Allow writes throughout the workspace for an isolated task')
    parser.add_argument('--allow-risky', action='store_true', help='Allow host shell, Docker and subagents')
    parser.add_argument('--max-seconds', type=float, default=cfg.max_run_time)
    args = parser.parse_args()
    if not cfg.model_api_key:
        parser.error('MINI_HARNESS_API_KEY or DEEPSEEK_API_KEY is required for model calls')
    if not args.workspace.is_dir():
        parser.error('workspace must exist')
    for name in (args.session, args.run_id):
        if name is not None and not name.replace('-', '').replace('_', '').isalnum():
            parser.error('session and run ID must contain only letters, digits, underscores or hyphens')
    if (args.write_workspace or args.run_id) and not args.task:
        parser.error('--write-workspace and --run-id require --task')
    def approve(name, params):
        return input(f'Allow {name} {params}? [y/N] ').strip().lower() == 'y'
    cfg = replace(cfg, work_space=args.workspace,
        sand_box=args.workspace if args.write_workspace else None, run_id=args.run_id or cfg.run_id,
        history_session_path=args.history_dir, user_name=args.session,
        max_run_time=args.max_seconds, risky_policy='allow' if args.allow_risky else ('deny' if args.task else 'ask'),
        approval=approve)
    agent = DeepSeekAgent(TOOLS, cfg)
    if args.task:
        result = agent.run_task(args.task)
        if args.telemetry_out:
            agent.dump_run(result, args.task, args.telemetry_out)
        print(result)
        return EXIT[result.outcome]
    agent.load_memory()
    manager = ToolManager(agent.tool_registry, agent.cfg)
    with OpenAI(api_key=cfg.model_api_key, base_url=cfg.base_url, max_retries=0) as client:
        while True:
            try:
                task = input('\nYou (/exit to quit): ').strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            if task == '/exit':
                return 0
            if not task:
                continue
            agent.cfg.deadline = None
            agent.messages.append({'role': 'user', 'content': task})
            agent.trajectory.append(agent.messages[-1])
            print(agent.run_turn(client, manager))


if __name__ == '__main__':
    raise SystemExit(main())
