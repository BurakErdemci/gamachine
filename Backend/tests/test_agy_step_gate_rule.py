"""The agy step-mode hook (`backend agy-hook`): allow rule and fail-closed paths.

The allowed shapes are the ones agy 1.2.8 was measured to emit for the unityai
bridge on Windows (PowerShell 5.1) on 25 Sep 2026; the attack corpus is what a
prefix match would have let through.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import agy_step_gate as gate

WIN_L = r"C:\Users\x\Backend\unityai.cmd"
POSIX_L = "/opt/gamachine/backend/unityai"

WIN_ALLOWED = [
    f'& "{WIN_L}" delete-file --path "notes/a.txt"',
    f'& "{WIN_L}" bash --command "git --version"',
    f"& '{WIN_L}' read-file --path 'Assets/Scripts/X.cs'",
    f'{WIN_L} list-dir --path Assets/Scripts',
    f'& "{WIN_L}" list-dir',
    f'& "{WIN_L.upper()}" delete-file --path "a.txt"',
    # the measured working write: single-quoted here-string piped to unityai
    "@'\nline one with \"double\" and 'single' quotes\n$HOME & | ; `tick` $(x)\nlast line\n'@ | "
    f'& "{WIN_L}" save-file --path "notes/a.txt" --content-stdin',
    "@'\r\n  '@ indented is still data in PowerShell 5.1\r\n'@ | "
    f'& "{WIN_L}" save-file --path "a.txt" --content-stdin\r\n',
    f'& "{WIN_L}" save-file --path "a.txt" --content "short text"',
    gate.PS_UTF8_PREFIX + "\n@'\nTürkçe şğü\n'@ | "
    f'& "{WIN_L}" save-file --path "a.txt" --content-stdin',
]

WIN_DENIED = [
    'python -c "open(\'x.txt\',\'w\').write(\'hi\')"',
    "Set-Content x.txt hi",
    f'& "{WIN_L}" bash --command "git status" && del x.txt',
    f'& "{WIN_L}" bash --command "git status"; del x.txt',
    f'& "{WIN_L}" bash --command "git status" | Out-File x.txt',
    f'& "{WIN_L}" bash --command "$(Remove-Item x.txt)"',
    f'& "{WIN_L}" bash --command "a`$b"',
    f"& \"{WIN_L}\" bash --command 'a&calc'",
    f'& "{WIN_L}" bash --command "a ^& calc"',
    f'& "{WIN_L}" bash --command "%COMSPEC% /c calc"',
    f'& "{WIN_L}" bash --command "echo x > y"',
    f'& "{WIN_L}" bash --command "a" extra',
    f'& "{WIN_L}" bash --command a&calc',
    f'& "{WIN_L}" bash --command "x\\" & calc & "y"',
    f'& "{WIN_L}" bash --command "(calc)"',
    f'& "{WIN_L}" bash --command "a!b"',
    f'& "{WIN_L}" bash --command "a"b',
    f'& "{WIN_L}" frobnicate --path "a"',
    f'& "{WIN_L}" delete-file --path "a" --path "b"',
    f'& "{WIN_L}" delete-file --command "a"',
    f'& "{WIN_L}" delete-file',
    f'& "{WIN_L}" bash',
    f'& "{WIN_L}" save-file --path "a" --content-stdin',
    f'"{WIN_L}" delete-file --path "a"',
    f'& "{WIN_L}.bak" delete-file --path "a"',
    f'& "C:\\evil\\unityai.cmd" delete-file --path "a"',
    f'$env:X="1"; & "{WIN_L}" delete-file --path "a"',
    f'cmd /c "{WIN_L}" delete-file --path "a"',
    f'& & "{WIN_L}" delete-file --path "a"',
    f'&"{WIN_L}" delete-file --path "a"',
    f'& "{WIN_L}"\tdelete-file --path "a"',
    f'& "{WIN_L}" delete-file --path "a"\ndel x',
    # here-string escapes
    "@\"\n$(Remove-Item x)\n\"@ | " f'& "{WIN_L}" save-file --path "a" --content-stdin',
    "@'\nbody\n'@ | " f'& "{WIN_L}" save-file --path "a" --content-stdin\ndel x',
    "@'\nbody\n'@; del x\n'@ | " f'& "{WIN_L}" save-file --path "a" --content-stdin',
    "@'\nbody\n'@ | " f'& "{WIN_L}" bash --command "x"',
    "@'\nbody\n'@ | " f'& "{WIN_L}" save-file --path "a" --content-stdin && del x',
    "@'\nbody\n'@|" f'& "{WIN_L}" save-file --path "a" --content-stdin',
    "@' \nbody\n'@ | " f'& "{WIN_L}" save-file --path "a" --content-stdin',
    "@'\nbody\r'@ | " f'& "{WIN_L}" save-file --path "a" --content-stdin',
    # only the exact encoding line may precede the here-string
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); del x\n@'\nb\n'@ | "
    f'& "{WIN_L}" save-file --path "a" --content-stdin',
    "$x = 1\n@'\nb\n'@ | " f'& "{WIN_L}" save-file --path "a" --content-stdin',
    gate.PS_UTF8_PREFIX + "\n" + gate.PS_UTF8_PREFIX + "\n@'\nb\n'@ | "
    f'& "{WIN_L}" save-file --path "a" --content-stdin',
    gate.PS_UTF8_PREFIX + "\n" f'& "{WIN_L}" delete-file --path "a"',
    # bash heredoc does not parse in PowerShell: refused on Windows
    f"{WIN_L} save-file --path a --content-stdin <<'UNITYAI_EOF'\nx\nUNITYAI_EOF",
]

POSIX_ALLOWED = [
    f"{POSIX_L} delete-file --path \"notes/a.txt\"",
    f"'{POSIX_L}' bash --command 'git --version'",
    f"{POSIX_L} save-file --path \"a.txt\" --content-stdin <<'UNITYAI_EOF'\n$(rm -rf ~) `x` & |\nUNITYAI_EOF",
    f"{POSIX_L} save-file --path \"a.txt\" --content-stdin <<'UNITYAI_EOF'\n  UNITYAI_EOF indented is data\nUNITYAI_EOF\n",
]

POSIX_DENIED = [
    f"{POSIX_L} save-file --path a --content-stdin <<'UNITYAI_EOF'\nx\nUNITYAI_EOF\nrm -rf ~",
    f"{POSIX_L} save-file --path a --content-stdin <<'UNITYAI_EOF'\nx\nUNITYAI_EOF\nUNITYAI_EOF",
    f"{POSIX_L} save-file --path a --content-stdin <<UNITYAI_EOF\n$(rm x)\nUNITYAI_EOF",
    f"{POSIX_L} save-file --path a --content-stdin <<-'UNITYAI_EOF'\nx\nUNITYAI_EOF",
    f"{POSIX_L} bash --command \"$(rm x)\"",
    f"{POSIX_L} bash --command 'x' ; rm x",
    f"& {POSIX_L} bash --command 'x'",
    f"X=1 {POSIX_L} bash --command 'x'",
    f"{POSIX_L} bash --command \"a\\\\\" x",
    "@'\nx\n'@ | " f"{POSIX_L} save-file --path a --content-stdin",
    f"sh -c '{POSIX_L} bash --command x'",
]


class TestAllowRule(unittest.TestCase):
    def test_windows_allowed_shapes(self):
        for command in WIN_ALLOWED:
            with self.subTest(command=command):
                self.assertTrue(gate.unityai_command_allowed(command, WIN_L, windows=True))

    def test_windows_attacks_are_denied(self):
        for command in WIN_DENIED:
            with self.subTest(command=command):
                self.assertFalse(gate.unityai_command_allowed(command, WIN_L, windows=True))

    def test_posix_allowed_shapes(self):
        for command in POSIX_ALLOWED:
            with self.subTest(command=command):
                self.assertTrue(gate.unityai_command_allowed(command, POSIX_L, windows=False))

    def test_posix_attacks_are_denied(self):
        for command in POSIX_DENIED:
            with self.subTest(command=command):
                self.assertFalse(gate.unityai_command_allowed(command, POSIX_L, windows=False))

    def test_empty_or_wrong_types_are_denied(self):
        for command, launcher in [("", WIN_L), (None, WIN_L), (f'& "{WIN_L}" list-dir', ""),
                                  (f'& "{WIN_L}" list-dir', None)]:
            self.assertFalse(gate.unityai_command_allowed(command, launcher, windows=True))


class TestDecide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "step-gate.json")
        gate.write_state(self.state, "step", WIN_L)

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, payload, state=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return gate.decide(raw, state or self.state, windows=True)["decision"]

    def run_call(self, command):
        return {"toolCall": {"name": "run_command", "args": {"CommandLine": command}}}

    def test_step_mode(self):
        self.assertEqual(self.decide(self.run_call(WIN_ALLOWED[0])), "allow")
        self.assertEqual(self.decide(self.run_call("python -c \"open('x','w')\"")), "deny")
        for name in gate.GATED_TOOLS:
            self.assertEqual(self.decide({"toolCall": {"name": name, "args": {}}}), "deny")

    def test_auto_mode_allows_everything(self):
        gate.write_state(self.state, "auto", WIN_L)
        self.assertEqual(self.decide(self.run_call("python -c \"open('x','w')\"")), "allow")
        self.assertEqual(self.decide({"toolCall": {"name": "write_to_file"}}), "allow")

    def test_fail_closed(self):
        missing = os.path.join(self.tmp.name, "nope.json")
        self.assertEqual(self.decide(self.run_call(WIN_ALLOWED[0]), state=missing), "deny")
        with open(self.state, "w") as f:
            f.write("{half")
        self.assertEqual(self.decide(self.run_call(WIN_ALLOWED[0])), "deny")
        gate.write_state(self.state, "sideways", WIN_L)
        self.assertEqual(self.decide(self.run_call(WIN_ALLOWED[0])), "deny")
        gate.write_state(self.state, "step", WIN_L)
        self.assertEqual(self.decide(b"not json"), "deny")
        self.assertEqual(self.decide({"toolCall": {"name": "run_command"}}), "deny")
        self.assertEqual(self.decide({"toolCall": {"name": "run_command", "args": {"CommandLine": 7}}}), "deny")
        self.assertEqual(self.decide({"toolCall": {"name": "some_new_tool"}}), "deny")

    def test_deny_reasons_reach_the_model(self):
        out = gate.decide(json.dumps(self.run_call("del x")).encode(), self.state, windows=True)
        self.assertIn("unityai", out["reason"])


class TestSubcommand(unittest.TestCase):
    """`backend agy-hook` through main.py, as agy's hook shim runs it (dev layout)."""

    def test_main_py_dispatches_agy_hook_without_loading_the_app(self):
        backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "s.json")
            gate.write_state(state, "step", WIN_L)
            cases = [({"toolCall": {"name": "run_command", "args": {"CommandLine": "del x"}}}, "deny"),
                     ({"toolCall": {"name": "write_to_file", "args": {}}}, "deny"),
                     (None, "deny")]
            for payload, want in cases:
                raw = b"garbage" if payload is None else json.dumps(payload).encode()
                started = time.monotonic()
                out = subprocess.run([sys.executable, os.path.join(backend, "app", "main.py"),
                                      "agy-hook", "--state", state],
                                     input=raw, capture_output=True, timeout=60)
                elapsed = time.monotonic() - started
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(json.loads(out.stdout.decode())["decision"], want)
                # The app (FastAPI, provider SDKs) is never imported on this path.
                self.assertLess(elapsed, 2.5)


if __name__ == "__main__":
    unittest.main()
