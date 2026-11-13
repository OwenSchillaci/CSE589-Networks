"""Lifecycle safety checks; live forwarding is exercised by make test-paths."""
import unittest
from unittest.mock import patch

import workflow


class WorkflowTests(unittest.TestCase):
    def test_shutdown_preserves_unmanaged_container(self):
        info = {'Config': {'Labels': {}}, 'State': {'Running': True}}
        with patch.object(workflow, 'inspect', return_value=info), \
                patch.object(workflow, 'run') as run, \
                patch.object(workflow, 'save_logs') as logs:
            workflow.stop('mn-stratum')
        run.assert_not_called()
        logs.assert_not_called()

    def test_failed_startup_rolls_back(self):
        with patch.object(workflow, 'inspect', return_value=None), \
                patch.object(workflow, 'make', side_effect=RuntimeError('build failed')), \
                patch.object(workflow, 'down') as down:
            with self.assertRaisesRegex(RuntimeError, 'build failed'):
                workflow.up()
        down.assert_called_once()

    def test_shutdown_attempts_all_components_after_error(self):
        calls = []

        def stop(name):
            calls.append(name)
            if name == workflow.CONTROLLERS[0]:
                raise RuntimeError('stop failed')

        with patch.object(workflow, 'stop', side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                workflow.down()
        self.assertEqual(calls, workflow.CONTROLLERS + [workflow.NETWORK])


if __name__ == '__main__':
    unittest.main()
