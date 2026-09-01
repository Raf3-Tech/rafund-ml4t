import sys
import types
import unittest


class TestMainDashboardCommand(unittest.TestCase):
    def test_dashboard_command_invokes_dashboard_launcher(self):
        calls = []

        def fake_dashboard_main(host=None, port=None):
            calls.append((host, port))

        fake_module = types.ModuleType("monitoring.run_dashboard")
        fake_module.main = fake_dashboard_main

        original_argv = sys.argv.copy()
        original_modules = sys.modules.copy()
        sys.argv = ["main.py", "dashboard", "--host", "127.0.0.1", "--port", "9000"]
        sys.modules["monitoring.run_dashboard"] = fake_module
        try:
            from main import main as cli_main

            self.assertEqual(cli_main(), 0)
            self.assertEqual(calls, [("127.0.0.1", 9000)])
        finally:
            sys.argv = original_argv
            sys.modules.clear()
            sys.modules.update(original_modules)
