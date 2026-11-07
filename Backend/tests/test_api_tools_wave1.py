"""API agent tools: web_fetch (address rules), todo_write, background shell jobs, and their registry schemas.

No test touches the network: web_fetch's resolver and transport are replaced.
"""
import io
import os
import subprocess
import sys
import textwrap
import time

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from agentic import shell_exec, shell_jobs, todo_list, web_fetch as wf  # noqa: E402
from tools import tool_registry  # noqa: E402


# ── web_fetch ────────────────────────────────────────────────────────────────

@pytest.fixture
def net(monkeypatch):
    """Fake resolver + transport. `net.sites` maps a host to a handler; `net.requests` records what was sent."""
    class Net:
        def __init__(self):
            self.dns = {}
            self.requests = []
            self.handler = lambda request: httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    state = Net()

    def handle(request):
        state.requests.append(request)
        return state.handler(request)

    monkeypatch.setattr(wf, "_resolve", lambda host, port: state.dns.get(host, ["93.184.216.34"]))
    monkeypatch.setattr(wf, "_transport", lambda: httpx.MockTransport(handle))
    return state


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://127.0.0.1:9999/",
    "http://localhost/",
    "http://[::1]/",
    "http://127.0.0.1:8080/mcp",
    "http://localhost:8000/",
    "http://10.0.0.1/",
    "http://192.168.1.1/admin",
    "http://172.16.5.4/",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/",
    "http://0.0.0.0/",
    "http://[::ffff:127.0.0.1]/",
    "http://2130706433/",
    "file:///C:/Windows/win.ini",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "example.com/no-scheme",
    "",
])
def test_web_fetch_refuses_local_private_and_odd_urls(net, url):
    result = wf.web_fetch(url)
    assert result["success"] is False and result["error"]
    assert net.requests == []


def test_web_fetch_refuses_a_name_that_resolves_to_loopback(net):
    net.dns["evil.example"] = ["127.0.0.1"]
    result = wf.web_fetch("http://evil.example/")
    assert result["success"] is False and "Private" in result["error"]
    assert net.requests == []


def test_web_fetch_refuses_when_any_resolved_address_is_private(net):
    net.dns["mixed.example"] = ["93.184.216.34", "10.1.2.3"]
    assert wf.web_fetch("https://mixed.example/")["success"] is False
    assert net.requests == []


def test_web_fetch_refuses_a_redirect_into_a_private_address(net):
    net.handler = lambda request: httpx.Response(302, headers={"location": "http://192.168.0.5/secret"})
    result = wf.web_fetch("https://public.example/start")
    assert result["success"] is False
    assert len(net.requests) == 1


def test_web_fetch_refuses_a_redirect_to_a_gamachine_port(net):
    net.handler = lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1:8080/"})
    assert wf.web_fetch("https://public.example/")["success"] is False
    assert len(net.requests) == 1


def test_web_fetch_refuses_a_redirect_to_a_name_that_resolves_private(net):
    net.dns["inner.example"] = ["10.0.0.9"]
    net.handler = lambda request: httpx.Response(301, headers={"location": "https://inner.example/"})
    assert wf.web_fetch("https://public.example/")["success"] is False
    assert len(net.requests) == 1


def test_web_fetch_stops_after_five_redirects(net):
    net.handler = lambda request: httpx.Response(302, headers={"location": "https://public.example/again"})
    result = wf.web_fetch("https://public.example/")
    assert result["success"] is False and "redirect" in result["error"]
    assert len(net.requests) == wf.MAX_REDIRECTS + 1


def test_web_fetch_follows_a_public_redirect(net):
    def handler(request):
        if request.headers["host"] == "a.example":
            return httpx.Response(302, headers={"location": "https://b.example/page"})
        return httpx.Response(200, text="landed", headers={"content-type": "text/plain"})
    net.handler = handler
    result = wf.web_fetch("https://a.example/")
    assert result["success"] is True and result["content"] == "landed"
    assert result["url"] == "https://b.example/page"


def test_web_fetch_pins_the_checked_address_and_keeps_the_host_name(net):
    net.dns["site.example"] = ["93.184.216.34"]
    wf.web_fetch("https://site.example:8443/p?q=1")
    request = net.requests[0]
    assert request.url.host == "93.184.216.34" and request.url.port == 8443
    assert request.headers["host"] == "site.example:8443"
    assert request.extensions["sni_hostname"] == "site.example"
    assert request.url.query == b"q=1"


def test_web_fetch_sends_no_cookies_or_credentials(net):
    def handler(request):
        if request.headers["host"] == "a.example":
            return httpx.Response(302, headers={"location": "https://b.example/", "set-cookie": "s=1"})
        return httpx.Response(200, text="x", headers={"content-type": "text/plain"})
    net.handler = handler
    wf.web_fetch("https://user:pw@a.example/")
    for request in net.requests:
        assert "cookie" not in request.headers and "authorization" not in request.headers


def test_web_fetch_refuses_binary_pages(net):
    net.handler = lambda request: httpx.Response(200, content=b"\x89PNG\r\n\x00\x00", headers={"content-type": "image/png"})
    result = wf.web_fetch("https://public.example/a.png")
    assert result["success"] is False and "text" in result["error"]


def test_web_fetch_refuses_an_untyped_binary_body(net):
    net.handler = lambda request: httpx.Response(200, content=b"abc\x00\x01def", headers={})
    assert wf.web_fetch("https://public.example/blob")["success"] is False


def test_web_fetch_reports_http_errors(net):
    net.handler = lambda request: httpx.Response(404, text="nope", headers={"content-type": "text/plain"})
    result = wf.web_fetch("https://public.example/missing")
    assert result["success"] is False and "404" in result["error"]


def test_web_fetch_converts_html_to_readable_text(net):
    page = textwrap.dedent("""\
        <!doctype html><html><head><title>My  Page</title><style>body{color:red}</style></head>
        <body><script>var secret = 1;</script><h1>Heading</h1><p>Hello <b>world</b>, see
        <a href="https://example.org/doc">the docs</a>.</p><h2>Part two</h2><ul><li>one</li><li>two</li></ul>
        <noscript>enable js</noscript></body></html>""")
    net.handler = lambda request: httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})
    text = wf.web_fetch("https://public.example/")["content"]
    assert "Title: My Page" in text
    assert "# Heading" in text and "## Part two" in text
    assert "Hello world, see the docs (https://example.org/doc)." in text
    assert "- one\n- two" in text
    assert "secret" not in text and "color:red" not in text and "enable js" not in text


def test_web_fetch_keeps_json_as_is(net):
    net.handler = lambda request: httpx.Response(200, text='{"a": 1}', headers={"content-type": "application/json"})
    assert wf.web_fetch("https://public.example/api")["content"] == '{"a": 1}'


def test_web_fetch_truncates_with_a_note(net):
    net.handler = lambda request: httpx.Response(200, text="x" * 500, headers={"content-type": "text/plain"})
    result = wf.web_fetch("https://public.example/", max_chars=100)
    assert result["content"].startswith("x" * 100) and "Truncated to 100" in result["content"]
    assert "x" * 101 not in result["content"]


def test_web_fetch_caps_the_body_it_reads(net, monkeypatch):
    monkeypatch.setattr(wf, "MAX_BODY_BYTES", 1000)
    net.handler = lambda request: httpx.Response(200, content=b"y" * 5000, headers={"content-type": "text/plain"})
    content = wf.web_fetch("https://public.example/", max_chars=100000)["content"]
    assert content.count("y") == 1000 and "cut" in content


def test_web_fetch_is_reachable_through_the_registry(net):
    result = tool_registry.execute_tool("web_fetch", {"url": "http://127.0.0.1/"}, ".", 7)
    assert result["success"] is False
    ok = tool_registry.execute_tool("web_fetch", {"url": "https://public.example/"}, ".", 7)
    assert ok["success"] is True and ok["content"] == "ok"


# ── todo_write ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_todos():
    todo_list._lists.clear()
    yield
    todo_list._lists.clear()


ITEMS = [
    {"content": "read the code", "status": "completed"},
    {"content": "write the fix", "status": "in_progress"},
    {"content": "run the tests", "status": "pending"},
]


def test_todo_write_stores_and_returns_the_whole_list():
    result = tool_registry.execute_tool("todo_write", {"items": ITEMS}, ".", 11)
    assert result["success"] is True and result["todos"] == ITEMS
    assert "[x] read the code" in result["content"] and "[~] write the fix" in result["content"]
    assert todo_list.get_todos(11) == ITEMS


def test_todo_write_replaces_the_previous_list():
    todo_list.set_todos(11, ITEMS)
    todo_list.set_todos(11, [{"content": "only one", "status": "pending"}])
    assert [i["content"] for i in todo_list.get_todos(11)] == ["only one"]


def test_todo_write_allows_only_one_in_progress_and_keeps_the_old_list():
    todo_list.set_todos(11, ITEMS)
    bad = [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}]
    result = todo_list.set_todos(11, bad)
    assert result["success"] is False and "in_progress" in result["error"]
    assert todo_list.get_todos(11) == ITEMS


@pytest.mark.parametrize("items", [
    "not a list", [1], [{"content": "", "status": "pending"}], [{"content": "a", "status": "doing"}],
    [{"status": "pending"}], [{"content": "x"}] * (todo_list.MAX_ITEMS + 1),
])
def test_todo_write_refuses_bad_input(items):
    assert todo_list.set_todos(5, items)["success"] is False
    assert todo_list.get_todos(5) == []


def test_todo_lists_are_per_conversation_and_the_id_is_not_a_model_argument():
    tool_registry.execute_tool("todo_write", {"items": ITEMS, "conversation_id": 99}, ".", 1)
    tool_registry.execute_tool("todo_write", {"items": [{"content": "other", "status": "pending"}]}, ".", 2)
    assert todo_list.get_todos(1) == ITEMS and todo_list.get_todos(99) == []
    assert [i["content"] for i in todo_list.get_todos(2)] == ["other"]


def test_todo_chats_kept_are_bounded(monkeypatch):
    monkeypatch.setattr(todo_list, "MAX_CHATS", 3)
    for chat in range(6):
        todo_list.set_todos(chat, ITEMS)
    assert list(todo_list._lists) == [3, 4, 5]


# ── shell jobs ───────────────────────────────────────────────────────────────

def _wait(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not reached in time")


def _alive(pid):
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _py(tmp_path, name, body):
    script = tmp_path / name
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


@pytest.fixture(autouse=True)
def _clean_jobs():
    yield
    for job in list(shell_jobs._jobs.values()):
        if job.status == shell_jobs.RUNNING:
            shell_jobs.stop(job.id, job.conversation_id)
    shell_jobs._jobs.clear()
    shell_jobs._listeners.clear()


def test_job_runs_in_background_then_exits_with_its_code_and_output(tmp_path):
    flag = tmp_path / "go"
    command = _py(tmp_path, "job.py", f"""
        import os, sys, time
        print("started", flush=True)
        while not os.path.exists({str(flag)!r}):
            time.sleep(0.05)
        print("finished", flush=True)
        sys.exit(3)
    """)
    started = time.time()
    job_id = shell_jobs.start(command, str(tmp_path), 1)
    assert time.time() - started < 5  # returned at once, did not wait for the child
    assert shell_jobs.status(job_id, 1)["status"] == shell_jobs.RUNNING
    _wait(lambda: "started" in shell_jobs.output(job_id, 1))
    assert shell_jobs.status(job_id, 1)["status"] == shell_jobs.RUNNING
    flag.write_text("x")
    done = _wait(lambda: shell_jobs.status(job_id, 1) if shell_jobs.status(job_id, 1)["status"] != "running" else None)
    assert done["status"] == shell_jobs.EXITED and done["exit_code"] == 3
    assert "finished" in shell_jobs.output(job_id, 1)
    assert shell_jobs.output(job_id, 1, tail=4).strip().endswith("ed") and len(shell_jobs.output(job_id, 1, tail=4)) == 4


def test_job_output_is_merged_stderr_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_jobs, "OUTPUT_KEEP_BYTES", 2000)
    command = _py(tmp_path, "noisy.py", """
        import sys
        sys.stderr.write("E" * 10)
        sys.stderr.flush()
        sys.stdout.write("o" * 20000)
        sys.stdout.write("END")
    """)
    job_id = shell_jobs.start(command, str(tmp_path), 1)
    _wait(lambda: shell_jobs.status(job_id, 1)["status"] != "running")
    text = shell_jobs.output(job_id, 1, tail=50000)
    assert text.endswith("END") and len(text) <= 2000 and "E" * 10 not in text


def test_stop_ends_the_whole_process_tree(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    (tmp_path / "grandchild.py").write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    parent = _py(tmp_path, "parent.py", f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, {str(tmp_path / 'grandchild.py')!r}])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        time.sleep(300)
    """)
    job_id = shell_jobs.start(parent, str(tmp_path), 1)
    pid = int(_wait(lambda: pid_file.exists() and pid_file.read_text() or None))
    assert _alive(pid)
    snapshot = shell_jobs.stop(job_id, 1)
    assert snapshot["status"] in (shell_jobs.RUNNING, shell_jobs.STOPPED)
    final = _wait(lambda: shell_jobs.status(job_id, 1) if shell_jobs.status(job_id, 1)["status"] != "running" else None)
    assert final["status"] == shell_jobs.STOPPED
    _wait(lambda: not _alive(pid))


def test_stopping_an_ended_job_changes_nothing(tmp_path):
    job_id = shell_jobs.start(_py(tmp_path, "quick.py", "print('hi')\n"), str(tmp_path), 1)
    _wait(lambda: shell_jobs.status(job_id, 1)["status"] != "running")
    assert shell_jobs.stop(job_id, 1)["status"] == shell_jobs.EXITED


def test_job_limit_per_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_jobs, "MAX_RUNNING_PER_CONVERSATION", 2)
    sleeper = _py(tmp_path, "sleep.py", "import time\ntime.sleep(300)\n")
    first = shell_jobs.start(sleeper, str(tmp_path), 1)
    shell_jobs.start(sleeper, str(tmp_path), 1)
    with pytest.raises(shell_jobs.JobError, match="Too many"):
        shell_jobs.start(sleeper, str(tmp_path), 1)
    shell_jobs.start(sleeper, str(tmp_path), 2)  # another chat has its own allowance
    shell_jobs.stop(first, 1)
    _wait(lambda: shell_jobs.status(first, 1)["status"] != "running")
    shell_jobs.start(sleeper, str(tmp_path), 1)  # a stopped job frees a slot


def test_listener_is_called_when_a_job_ends(tmp_path):
    seen = []
    shell_jobs.add_job_listener(seen.append)
    job_id = shell_jobs.start(_py(tmp_path, "quick.py", "import sys\nsys.exit(2)\n"), str(tmp_path), 5)
    _wait(lambda: seen)
    assert seen[0]["job_id"] == job_id and seen[0]["status"] == shell_jobs.EXITED
    assert seen[0]["exit_code"] == 2 and seen[0]["conversation_id"] == 5


def test_a_failing_listener_does_not_break_the_others(tmp_path):
    seen = []

    def boom(_):
        raise RuntimeError("listener bug")

    shell_jobs.add_job_listener(boom)
    shell_jobs.add_job_listener(seen.append)
    shell_jobs.start(_py(tmp_path, "quick.py", "print(1)\n"), str(tmp_path), 1)
    _wait(lambda: seen)


def test_jobs_belong_to_their_conversation(tmp_path):
    job_id = shell_jobs.start(_py(tmp_path, "quick.py", "print(1)\n"), str(tmp_path), 1)
    for call in (shell_jobs.status, shell_jobs.output, shell_jobs.stop):
        with pytest.raises(shell_jobs.JobError, match="Unknown job"):
            call(job_id, 2)
    with pytest.raises(shell_jobs.JobError):
        shell_jobs.status("job-999999", 1)


def test_finished_jobs_kept_per_conversation_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_jobs, "MAX_JOBS_PER_CONVERSATION", 2)
    quick = _py(tmp_path, "quick.py", "print(1)\n")
    ids = []
    for _ in range(4):
        ids.append(shell_jobs.start(quick, str(tmp_path), 1))
        _wait(lambda: shell_jobs.status(ids[-1], 1)["status"] != "running")
    assert [j["job_id"] for j in shell_jobs.list_jobs(1)] == ids[-2:]


def test_background_run_command_goes_through_the_same_refusal_as_foreground(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(shell_exec, "shell_refusal", lambda command, workspace: "protected file")
    monkeypatch.setattr(shell_exec, "start_background", lambda *a: started.append(a))
    foreground = tool_registry.execute_tool("run_command", {"command": "echo hi"}, str(tmp_path), 1)
    background = tool_registry.execute_tool(
        "run_command", {"command": "echo hi", "background": True}, str(tmp_path), 1)
    assert foreground == {"success": False, "error": "protected file"}
    assert background == {"success": False, "error": "protected file"}
    assert started == []


def test_background_run_command_uses_the_foreground_argv_decision(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(shell_exec, "plan", lambda command, workspace: ["echo", "hi"])

    class Done:
        stdout = io.BytesIO()

        def wait(self):
            return 0

    monkeypatch.setattr(shell_exec, "start_background",
                        lambda command, argv, workspace: calls.append((command, argv, workspace)) or Done())
    result = tool_registry.execute_tool("run_command", {"command": "echo hi", "background": "true"}, str(tmp_path), 1)
    assert result["success"] is True and result["job_id"].startswith("job-")
    assert calls == [("echo hi", ["echo", "hi"], str(tmp_path))]


def test_job_tools_work_through_the_registry(tmp_path):
    flag = tmp_path / "go"
    command = _py(tmp_path, "job.py", f"""
        import os, time
        print("hello-job", flush=True)
        while not os.path.exists({str(flag)!r}):
            time.sleep(0.05)
    """)
    started = tool_registry.execute_tool("run_command", {"command": command, "background": True}, str(tmp_path), 9)
    assert started["success"] is True and started["status"] == "running"
    job_id = started["job_id"]
    _wait(lambda: "hello-job" in tool_registry.execute_tool("job_output", {"job_id": job_id}, ".", 9)["content"])
    assert tool_registry.execute_tool("job_status", {"job_id": job_id}, ".", 9)["status"] == "running"
    assert tool_registry.execute_tool("job_status", {"job_id": job_id}, ".", 8)["success"] is False
    stopped = tool_registry.execute_tool("job_stop", {"job_id": job_id}, ".", 9)
    assert stopped["success"] is True
    _wait(lambda: tool_registry.execute_tool("job_status", {"job_id": job_id}, ".", 9)["status"] == "stopped")
    assert tool_registry.execute_tool("job_output", {"job_id": "nope"}, ".", 9)["success"] is False


def test_foreground_run_command_is_unchanged(tmp_path):
    result = tool_registry.execute_tool("run_command", {"command": _py(tmp_path, "p.py", "print('fg')\n")}, str(tmp_path), 1)
    assert result["success"] is True and "fg" in result["stdout"] and result["exit_code"] == 0


# ── registry schemas ─────────────────────────────────────────────────────────

NEW_TOOLS = {"web_fetch", "todo_write", "job_status", "job_output", "job_stop"}


def _definitions():
    return {t["name"]: t for t in tool_registry.TOOL_DEFINITIONS}


def test_new_tools_are_registered_with_valid_schemas():
    defs = _definitions()
    assert NEW_TOOLS <= set(defs)
    for name in NEW_TOOLS | {"run_command"}:
        schema = defs[name]["parameters"]
        assert defs[name]["description"] and schema["type"] == "object"
        assert set(schema["required"]) <= set(schema["properties"])
        assert name in tool_registry._TOOL_FUNCTIONS
    assert defs["run_command"]["parameters"]["properties"]["background"]["type"] == "boolean"
    assert defs["run_command"]["parameters"]["required"] == ["command"]
    assert defs["web_fetch"]["parameters"]["required"] == ["url"]
    todo_item = defs["todo_write"]["parameters"]["properties"]["items"]["items"]
    assert todo_item["properties"]["status"]["enum"] == ["pending", "in_progress", "completed"]


def test_tool_names_stay_unique():
    names = [t["name"] for t in tool_registry.TOOL_DEFINITIONS]
    assert len(names) == len(set(names))


def test_new_tools_reach_the_gemini_and_openai_declarations():
    gemini = {d["name"]: d for d in tool_registry.get_gemini_tool_declarations()[0]["function_declarations"]}
    openai = {d["function"]["name"]: d["function"] for d in tool_registry.get_openai_tool_declarations()}
    assert NEW_TOOLS <= set(gemini) and NEW_TOOLS <= set(openai)
    assert gemini["run_command"]["parameters"]["properties"]["background"]["type"] == "boolean"
    items = gemini["todo_write"]["parameters"]["properties"]["items"]
    assert items["type"] == "array" and items["items"]["properties"]["status"]["enum"] == [
        "pending", "in_progress", "completed"]
    assert items["items"]["required"] == ["content", "status"]
    assert openai["web_fetch"]["parameters"]["properties"]["max_chars"]["type"] == "integer"
