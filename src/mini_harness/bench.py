"""Local, serial benchmark runner. Only run trusted manifests/verifiers."""
import argparse
from dataclasses import asdict, replace
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from mini_harness.agent import DeepSeekAgent
from mini_harness.config import CONFIG
from mini_harness.process import run_command
from mini_harness.tool.box import TOOLS
from mini_harness.tool.utils import atomic_write


def save(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def load_tasks(path):
    tasks = json.loads(Path(path).read_text(encoding='utf-8'))
    ids = set()
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('Manifest must be a nonempty list')
    for t in tasks:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', t['id']) or t['id'] in ids:
            raise ValueError('Task IDs must be unique safe filenames')
        ids.add(t['id'])
        if not isinstance(t['task'], str) or not t['task'].strip():
            raise ValueError('Missing task description')
        if not isinstance(t['verify'], str) or not t['verify'].strip():
            raise ValueError('Missing independent Python verifier')
        for name, content in t.get('files', {}).items():
            if Path(name).is_absolute() or '..' in Path(name).parts or not isinstance(content, str):
                raise ValueError('Unsafe fixture path')
        if t.get('max_seconds', 120) <= 0 or t.get('verify_seconds', 30) <= 0:
            raise ValueError('Budgets must be positive')
    return tasks


def run_suite(manifest, output, cfg=CONFIG, resume=False, agent_factory=DeepSeekAgent):
    tasks = load_tasks(manifest)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
    source_hash = hashlib.sha256()
    for p in sorted(Path(__file__).parent.rglob('*.py')):
        source_hash.update(p.relative_to(Path(__file__).parent).as_posix().encode())
        source_hash.update(p.read_bytes())
    identity = dict(manifest_sha256=fingerprint, source_sha256=source_hash.hexdigest(),
                    model=cfg.model_name, base_url=cfg.base_url, max_token=cfg.max_token,
                    max_turn=cfg.max_turn, risky_policy=cfg.risky_policy)
    meta = output / 'metadata.json'
    if meta.exists() and (not resume or json.loads(meta.read_text(encoding='utf-8')) != identity):
        raise ValueError('Output exists or resume configuration differs; use a new directory')
    save(meta, identity)
    rows = []
    for task in tasks:
        root = output / task['id']
        result_path = root / 'result.json'
        if resume and result_path.exists():
            rows.append(json.loads(result_path.read_text(encoding='utf-8')))
            continue
        # Never recycle partial work: preserve the failed attempt for inspection.
        attempt = root / str(time.time_ns())
        workspace = attempt / 'workspace'
        workspace.mkdir(parents=True)
        row = dict(id=task['id'], passed=False, status='environment_error', attempt=str(attempt))
        try:
            for name, content in task.get('files', {}).items():
                target = (workspace / name).resolve()
                if not target.is_relative_to(workspace):
                    raise ValueError('Fixture escapes workspace')
                atomic_write(target, content)
            local_cfg = replace(cfg, work_space=workspace, sand_box=workspace,
                history_session_path=str(attempt / 'logs'), run_id=task['id'], deadline=None,
                max_run_time=task.get('max_seconds', 120), child_results=[])
            agent = agent_factory(TOOLS, local_cfg)
            result = agent.run_task(task['task'])
            row.update(agent=asdict(result), status='failed')
            # Verifier remains outside the agent workspace and runs after the agent stops.
            verifier = attempt / 'verify.py'
            atomic_write(verifier, task['verify'])
            code, log = run_command([sys.executable, '-I', str(verifier)], workspace,
                                   task.get('verify_seconds', 30), cfg.bash_env)
            atomic_write(attempt / 'verify.log', log)
            row.update(verifier_exit=code, passed=code == 0,
                       status='passed' if code == 0 else 'failed')
            diff = []
            names = set(task.get('files', {})) | {str(p.relative_to(workspace)) for p in workspace.rglob('*') if p.is_file() and not p.is_symlink()}
            for name in sorted(names):
                target = workspace / name
                if target.is_symlink() or not target.resolve().is_relative_to(workspace):
                    continue
                try:
                    after = target.read_text(encoding='utf-8') if target.exists() else ''
                except (UnicodeError, OSError):
                    continue
                before = task.get('files', {}).get(name, '')
                diff.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                fromfile='a/' + name, tofile='b/' + name))
            atomic_write(attempt / 'changes.diff', ''.join(diff))
        except TimeoutError as e:
            row.update(status='timeout', error=str(e))
        except Exception as e:
            row.update(error=f'{type(e).__name__}: {e}')
        save(result_path, row)
        rows.append(row)
        save(output / 'results.json', rows)
    summary = dict(total=len(rows), passed=sum(r['passed'] for r in rows),
        pass_rate=sum(r['passed'] for r in rows) / len(rows),
        environment_errors=sum(r['status'] == 'environment_error' for r in rows),
        agent_timeouts=sum(r.get('agent', {}).get('outcome') == 'timeout' for r in rows),
        agent_errors=sum(r.get('agent', {}).get('outcome') == 'error' for r in rows),
        prompt_tokens=sum(r.get('agent', {}).get('prompt_total', 0) for r in rows),
        completion_tokens=sum(r.get('agent', {}).get('completion_total', 0) for r in rows),
        wall_seconds=sum(r.get('agent', {}).get('wall', 0) for r in rows))
    save(output / 'results.json', rows)
    save(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--allow-risky', action='store_true')
    args = parser.parse_args()
    if not CONFIG.model_api_key:
        parser.error('DEEPSEEK_API_KEY is required')
    cfg = replace(CONFIG, risky_policy='allow' if args.allow_risky else 'deny')
    print(json.dumps(run_suite(args.manifest, args.output, cfg, args.resume), indent=2))


if __name__ == '__main__':
    main()
