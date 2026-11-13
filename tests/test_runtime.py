import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from mini_harness.config import config
from mini_harness.tool.box import TOOLS
from mini_harness.tool.tool_manager import ToolManager
from mini_harness.agent import DeepSeekAgent, Result
from mini_harness.bench import run_suite
from mini_harness.process import run_command


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / '.cache' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config(work_space=self.root, sand_box=self.root,
                          history_session_path=str(self.root / 'logs'))
        self.manager = ToolManager({t.name: t for t in TOOLS}, self.cfg)

    def call(self, name, **args):
        return self.manager.execute_tool(NS(id='call1', function=NS(name=name, arguments=json.dumps(args))))

    def test_partial_read_cannot_overwrite(self):
        p = self.root / 'a.txt'
        p.write_text('one\ntwo\n')
        self.assertTrue(self.call('read_file', path='a.txt', limit=1).ok)
        self.assertEqual(self.call('write_file', path='a.txt', content='lost', overwrite=True).tag, 'need_full')
        self.assertEqual(p.read_text(), 'one\ntwo\n')
        self.assertTrue(self.call('read_file', path='a.txt').ok)
        self.assertTrue(self.call('write_file', path='a.txt', content='new', overwrite=True).ok)

    def test_sensitive_denial_does_not_disclose(self):
        (self.root / '.env').write_text('PRIVATE_VALUE')
        result = self.call('edit_file', path='.env', old_string='x', new_string='y')
        self.assertEqual(result.tag, 'denied')
        self.assertNotIn('PRIVATE_VALUE', result.content)

    def test_changed_content_invalidates_read(self):
        p = self.root / 'a.txt'
        p.write_text('one')
        self.call('read_file', path='a.txt')
        p.write_text('two')
        self.assertEqual(self.call('edit_file', path='a.txt', old_string='two', new_string='three').tag, 'stale')

    def test_todo(self):
        result = self.call('run_todo', todos=[dict(task='fix', activeform='fixing', status='in_progress')])
        self.assertTrue(result.ok)
        self.assertIn('fixing', result.content)

    def test_shell_policy_and_failure(self):
        self.assertEqual(self.call('run_bash', command='exit 7').tag, 'denied')
        self.cfg.risky_policy = 'allow'
        self.assertFalse(self.call('run_bash', command='exit 7').ok)

    def test_validation(self):
        self.assertEqual(self.call('read_file', path='a', offset=0).tag, 'invalid_args')
        self.assertEqual(self.call('missing').tag, 'unknown_tool')

    def test_relative_workspace_and_search(self):
        (self.root / 'a.txt').write_text('needle')
        self.assertTrue(self.call('grep_file', path='.', glob='*.txt', pattern='needle').ok)
        self.assertTrue(self.call('edit_file', path='a.txt', old_string='needle', new_string='done').ok)

    def test_timeout_saves(self):
        agent = DeepSeekAgent([], self.cfg)
        agent.cfg.deadline = time.monotonic() - 1
        result = agent.run_turn(None, self.manager)
        self.assertEqual(result.outcome, 'timeout')
        self.assertTrue((self.root / 'logs' / 'the_user.json').exists())

    def test_mock_completion(self):
        agent = DeepSeekAgent([], self.cfg)
        message = NS(tool_calls=None, model_dump=lambda **kw: dict(role='assistant', content='done'))
        with patch.object(agent, 'request_agent', return_value=(NS(choices=[NS(message=message)]), None)):
            result = agent.run_turn(None, self.manager)
        self.assertEqual(result.outcome, 'completed')

    def test_missing_history(self):
        agent = DeepSeekAgent([], self.cfg)
        agent.load_memory()
        self.assertIsInstance(agent.messages, list)

    def test_process_timeout_and_output_bound(self):
        import sys
        with self.assertRaises(TimeoutError):
            run_command([sys.executable, '-c', 'import time; time.sleep(10)'], self.root, .1)
        code, text = run_command([sys.executable, '-c', 'print("x"*10000)'], self.root, 5, limit=100)
        self.assertEqual(code, 0)
        self.assertLess(len(text), 200)

    def test_benchmark_verifies_instead_of_trusting_completion(self):
        class FakeAgent:
            def __init__(self, tools, cfg):
                pass
            def run_task(self, task):
                return Result('completed', 0, 1, 0, {}, {}, 0, 0, 0, .01)
        manifest = self.root / 'tasks.json'
        manifest.write_text(json.dumps([dict(id='fails', task='do work', verify='raise AssertionError()')]))
        out = self.root / 'output'
        summary = run_suite(manifest, out, self.cfg, agent_factory=FakeAgent)
        self.assertEqual(summary['passed'], 0)
        self.assertEqual(summary['environment_errors'], 0)
        again = run_suite(manifest, out, self.cfg, resume=True, agent_factory=FakeAgent)
        self.assertEqual(summary, again)

    def test_tool_loop(self):
        agent = DeepSeekAgent(TOOLS, self.cfg)
        call = NS(id='c1', function=NS(name='run_todo', arguments='{"todos": []}'))
        tool_message = NS(tool_calls=[call], model_dump=lambda **kw: dict(role='assistant', content=None,
            tool_calls=[dict(id='c1', type='function', function=dict(name='run_todo', arguments='{"todos": []}'))]))
        done = NS(tool_calls=None, model_dump=lambda **kw: dict(role='assistant', content='done'))
        responses = [(NS(choices=[NS(message=m)]), None) for m in [tool_message, done]]
        with patch.object(agent, 'request_agent', side_effect=responses):
            result = agent.run_turn(None, self.manager)
        self.assertEqual((result.outcome, result.calls, result.ok), ('completed', 1, 1))
        self.assertEqual(agent.messages[-2]['tool_call_id'], 'c1')

    def test_compaction_keeps_tool_group(self):
        agent = DeepSeekAgent([], self.cfg)
        agent.cfg.context_chars = 18000
        agent.cfg.recent_keep = 2
        agent.messages = [dict(role='system', content='system'), dict(role='user', content='original task')]
        agent.messages += [dict(role='user', content='x' * 5000) for _ in range(5)]
        agent.messages += [dict(role='assistant', content='', tool_calls=[dict(id='a')]),
                           dict(role='tool', tool_call_id='a', content='result'),
                           dict(role='assistant', content='final')]
        agent.compact()
        self.assertEqual(agent.messages[1]['content'], 'original task')
        self.assertEqual(agent.messages[-3]['tool_calls'][0]['id'], 'a')

    def test_child_tokens_included(self):
        agent = DeepSeekAgent([], self.cfg)
        def request(*args):
            agent.cfg.child_results.append(dict(prompt_total=7, completion_total=3))
            m = NS(tool_calls=None, model_dump=lambda **kw: dict(role='assistant', content='done'))
            return NS(choices=[NS(message=m)]), None
        with patch.object(agent, 'request_agent', side_effect=request):
            result = agent.run_turn(None, self.manager)
        self.assertEqual((result.prompt_total, result.completion_total), (7, 3))

    def test_interrupt_persists(self):
        agent = DeepSeekAgent([], self.cfg)
        with patch.object(agent, 'request_agent', side_effect=KeyboardInterrupt):
            result = agent.run_turn(None, self.manager)
        self.assertEqual(result.outcome, 'interrupted')
        self.assertTrue((self.root / 'logs' / 'the_user.json').exists())


    def test_refusal_echoes_current_content(self):
        p = self.root / 'a.txt'
        p.write_text('alpha\nbeta\n')
        result = self.call('edit_file', path='a.txt', old_string='alpha', new_string='gamma')
        self.assertEqual(result.tag, 'need_read')
        self.assertIn('alpha', result.content)

    def test_edit_echoes_new_lines(self):
        p = self.root / 'a.txt'
        p.write_text('one\ntwo\nthree\n')
        self.assertTrue(self.call('read_file', path='a.txt').ok)
        result = self.call('edit_file', path='a.txt', old_string='two', new_string='TWO')
        self.assertTrue(result.ok)
        self.assertIn('\tTWO', result.content)
        self.assertEqual(p.read_text(), 'one\nTWO\nthree\n')

    def test_duplicate_call_rejected(self):
        self.assertTrue(self.call('run_todo', todos=[]).ok)
        self.assertEqual(self.call('run_todo', todos=[]).tag, 'duplicate')

    def test_command_timeout_is_tool_failure(self):
        import sys
        self.cfg.risky_policy = 'allow'
        self.cfg.bash_timeout = 0.2
        result = self.call(
            'run_bash',
            command=f'"{sys.executable}" -c "import time; time.sleep(5)"'
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.tag, 'execute_failed')
        self.assertIn('exceeded', result.content)

    def test_swe_task_entry_writes_to_repo_and_exports_trace(self):
        from mini_harness.main import main

        source = self.root / "source.py"
        source.write_text("before")
        args = ["mini-harness", "--task", "fix source.py", "--workspace", str(self.root),
                "--write-workspace", "--run-id", "mini_harness",
                "--history-dir", self.cfg.history_session_path,
                "--telemetry-out", str(self.root / "telemetry.json")]
        self.cfg.model_api_key = "test-only"

        def tool_message(name, **params):
            function = dict(name=name, arguments=json.dumps(params))
            call = dict(id=name, type="function", function=function)
            return NS(tool_calls=[NS(id=name, function=NS(**function))],
                      model_dump=lambda **kw: dict(role="assistant", tool_calls=[call]))

        messages = [
            tool_message("read_file", path="source.py"),
            tool_message("edit_file", path="source.py", old_string="before", new_string="after"),
            NS(tool_calls=None, model_dump=lambda **kw: dict(role="assistant", content="done")),
        ]
        responses = [(NS(choices=[NS(message=m)]), None) for m in messages]
        with patch("sys.argv", args), patch.object(DeepSeekAgent, "request_agent", side_effect=responses):
            self.assertEqual(main(self.cfg), 0)
        self.assertEqual(source.read_text(), "after")
        trace = self.root / "logs" / "mini_harness.trajectory.json"
        self.assertEqual(len(json.loads(trace.read_text(encoding="utf-8"))), 7)
        tele = json.loads((self.root / "telemetry.json").read_text(encoding="utf-8"))
        self.assertEqual((tele["run_id"], tele["ok"]), ("mini_harness", 2))


if __name__ == '__main__':
    unittest.main()
