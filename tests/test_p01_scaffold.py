import asyncio
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolRequestParams

import mock_mcp_server as contracts
from src.mcp_server.runner import WorkerFailure, WorkerRunner
from src.mcp_server.server import ServerScaffold
import src.mcp_server.server as server_module

ROOT = Path(__file__).resolve().parents[1]
SECRET = "SYNTHETIC_CHILD_SECRET"
ARGS = dict(schema_version="1.0.0", session_id="p01-session", trace_id="p01-trace",
            logical_tool_call_id="p01-logical", attempt_id="p01-attempt", unit="nginx.service")


def argv(mode):
    return (sys.executable, "-I", str(ROOT / "tests/p01_worker_fixture.py"), mode)


def validate(result, tool="service_status"):
    response = result.structured_content
    contracts.TOOLS[tool][4].validate(response)
    assert json.loads(result.content[0].text) == response
    assert result.is_error == (response["status"] == "error")
    assert SECRET not in result.model_dump_json()
    assert contracts.wire_size(result, 1) <= 65536
    return response


@pytest.fixture
def actual_sdk_frames(monkeypatch):
    import mcp.client.stdio as transport
    original = transport._parse_line
    frames = []
    def parse(line):
        frame = json.loads(line)
        if "structuredContent" in frame.get("result", {}):
            frames.append(len((line + "\n").encode("utf-8")))
        return original(line)
    monkeypatch.setattr(transport, "_parse_line", parse)
    yield frames
    assert frames and all(size <= 65536 for size in frames)


def test_default_scaffold_fails_closed_without_linux_collector():
    async def exercise():
        app = ServerScaffold()
        result = await app.call_tool(SimpleNamespace(request_id=1), CallToolRequestParams(name="service_status", arguments=ARGS))
        response = validate(result)
        assert response["error_code"] == "UNSUPPORTED"
        assert response["evidence"] == [] and app.runner.process is None
    asyncio.run(exercise())


def test_root_identity_is_rejected_without_privilege_change(monkeypatch):
    monkeypatch.setattr(server_module, "os", SimpleNamespace(name="posix", geteuid=lambda: 0))
    with pytest.raises(RuntimeError, match="Refusing root"):
        server_module.require_reader_identity()


def test_no_shell_execution_or_write_profile():
    with pytest.raises(ValueError):
        ServerScaffold(backends={"restart_service": argv("small")})
    for path in [ROOT / "run_mcp_server.py", *list((ROOT / "src/mcp_server").glob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                assert not (getattr(node.func, "attr", "") in {"create_subprocess_shell", "system", "popen"})
                assert not any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant)
                               and keyword.value.value is True for keyword in node.keywords)


@pytest.mark.parametrize("mode,code", [("sleep", "TIMEOUT"), ("stdout_flood", "OUTPUT_LIMIT"),
    ("infinite_stdout", "OUTPUT_LIMIT"),
    ("stderr_flood", "OUTPUT_LIMIT"), ("failure", "EXECUTION_UNKNOWN"),
    ("malformed", "EXECUTION_UNKNOWN"), ("wrong_ids", "EXECUTION_UNKNOWN"),
    ("invalid_output", "EXECUTION_UNKNOWN"), ("semantic", "INVALID_INPUT"),
    ("permission", "PERMISSION_DENIED"), ("large_atomic", "OUTPUT_LIMIT")])
def test_worker_faults_safe_and_schema_valid(mode, code, capfd):
    async def exercise():
        runner = WorkerRunner(timeout=0.1 if mode == "sleep" else 10)
        app = ServerScaffold(backends={"service_status": argv(mode)}, runner=runner)
        response = validate(await app.call_tool(SimpleNamespace(request_id=1),
            CallToolRequestParams(name="service_status", arguments=ARGS)))
        assert response["error_code"] == code
        assert response["session_id"] is None and response["trace_id"] == "invalid-input"
        assert runner.process is None and runner.spawn is None
    asyncio.run(exercise())
    captured = capfd.readouterr()
    assert SECRET not in captured.out + captured.err


def test_invalid_input_never_spawns_and_unknown_fields_reject():
    class NeverRun:
        async def run(self, *args):
            raise AssertionError("invalid input reached worker")
    async def exercise():
        app = ServerScaffold(backends={"service_status": argv("small")}, runner=NeverRun())
        for arguments in [{**ARGS, "trace_id": SECRET, "approved": True},
                          {key: value for key, value in ARGS.items() if key != "unit"},
                          {**ARGS, "unit": "../../private.service"}, {**ARGS, "unit": None}]:
            response = validate(await app.call_tool(SimpleNamespace(request_id=1),
                CallToolRequestParams(name="service_status", arguments=arguments)))
            assert response["error_code"] == "INVALID_INPUT"
    asyncio.run(exercise())


def test_cancellation_reaps_owned_child_and_runner_reusable(monkeypatch):
    async def exercise():
        real_spawn = asyncio.create_subprocess_exec
        spawned = []
        ready = asyncio.Event()
        async def track(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            spawned.append(process)
            ready.set()
            return process
        monkeypatch.setattr(asyncio, "create_subprocess_exec", track)
        runner = WorkerRunner()
        task = asyncio.create_task(runner.run(argv("sleep"), ARGS))
        await asyncio.wait_for(ready.wait(), 5)
        with pytest.raises(WorkerFailure, match="POLICY_DENIED"):
            await runner.run(argv("small"), ARGS)
        assert len(spawned) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(process.returncode is not None for process in spawned)
        assert runner.process is None and runner.spawn is None
        assert json.loads((await runner.run(argv("small"), ARGS)))['status'] == 'ok'
        assert await runner.close()
    asyncio.run(exercise())


def test_runner_rejects_untrusted_executable_and_busy_call():
    async def exercise():
        runner = WorkerRunner()
        with pytest.raises(WorkerFailure, match="POLICY_DENIED"):
            await runner.run(("python", "-c", "print('not run')"), ARGS)
        runner.closing = True
        with pytest.raises(WorkerFailure, match="POLICY_DENIED"):
            await runner.run(argv("small"), ARGS)
    asyncio.run(exercise())


def test_unconfirmed_cleanup_retains_ownership_and_refuses_work():
    class Unconfirmed:
        returncode = None
        def terminate(self):
            raise OSError("SYNTHETIC_CHILD_SECRET")
    async def exercise():
        runner = WorkerRunner()
        owned = Unconfirmed()
        runner.process = owned
        assert not await runner.close()
        assert runner.process is owned and runner.closing
        with pytest.raises(WorkerFailure, match="POLICY_DENIED"):
            await runner.run(argv("small"), ARGS)
    asyncio.run(exercise())


def test_secret_environment_not_inherited_by_child(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    async def exercise():
        app = ServerScaffold(backends={"service_status": argv("environment")})
        response = validate(await app.call_tool(SimpleNamespace(request_id=1),
            CallToolRequestParams(name="service_status", arguments=ARGS)))
        assert response["data"]["description"] == "environment-sanitized"
    asyncio.run(exercise())


def test_infinite_output_confirms_child_exit_and_pipe_eof(monkeypatch, capfd):
    async def exercise():
        original = asyncio.create_subprocess_exec
        processes = []
        async def track(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            return process
        monkeypatch.setattr(asyncio, "create_subprocess_exec", track)
        runner = WorkerRunner()
        with pytest.raises(WorkerFailure, match="OUTPUT_LIMIT"):
            await runner.run(argv("infinite_stdout"), ARGS)
        assert len(processes) == 1 and processes[0].returncode is not None
        assert processes[0].stdout.at_eof() and processes[0].stderr.at_eof()
        assert runner.process is None and runner.drain_task is None
        assert await runner.close()
    asyncio.run(exercise())
    import gc
    gc.collect()
    captured = capfd.readouterr()
    assert "Event loop is closed" not in captured.err
    assert SECRET not in captured.out + captured.err


def test_schema_valid_partial_keeps_whole_records_and_metadata():
    async def exercise():
        app = ServerScaffold(backends={"query_journal": argv("partial")})
        args = {**ARGS, "since":"2026-11-01T00:00:00Z", "until":"2026-11-05T00:00:00Z"}
        response = validate(await app.call_tool(SimpleNamespace(request_id="X" * 126),
            CallToolRequestParams(name="query_journal", arguments=args)), "query_journal")
        assert response["status"] == "partial" and response["truncated"]
        assert 0 < response["data"]["count"] < 100
        assert response["trace_id"] == ARGS["trace_id"]
        assert all(row["message"] == "whole-record-" + "漢" * 150 for row in response["data"]["entries"])
        assert contracts.wire_size(app.native(response), "X" * 126) <= 65536
    asyncio.run(exercise())


def test_sdk_handshake_discovery_default_and_fixture_paths(capfd, actual_sdk_frames):
    async def exercise():
        for fixtures, mode in ((False, "auto"), (True, "auto"), (True, "legacy")):
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / "run_mcp_server.py"), *(["--fixtures"] if fixtures else [])])
            async with Client(params, mode=mode, read_timeout_seconds=15) as client:
                tools = (await client.list_tools()).tools
                assert len(tools) == 12
                for tool in tools:
                    assert tool.input_schema == contracts.TOOLS[tool.name][1]
                    assert tool.output_schema == contracts.TOOLS[tool.name][2]
                response = validate(await client.call_tool("service_status", ARGS))
                assert response["status"] == ("ok" if fixtures else "error")
                if fixtures:
                    assert response["evidence"][0]["source"] == "synthetic.mock.service_status"
                else:
                    assert response["error_code"] == "UNSUPPORTED"
                bad = validate(await client.call_tool("service_status", {**ARGS, "trace_id": SECRET, "approved": True}))
                assert bad["error_code"] == "INVALID_INPUT"
                restart = await client.call_tool("restart_service", {**{k:v for k,v in ARGS.items()}, "mode":"propose", "reason":"test"})
                assert validate(restart, "restart_service")["error_code"] == "UNSUPPORTED"
    asyncio.run(exercise())
    captured = capfd.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("mode,code", [("sleep", "TIMEOUT"), ("stdout_flood", "OUTPUT_LIMIT"),
    ("stderr_flood", "OUTPUT_LIMIT"), ("malformed", "EXECUTION_UNKNOWN"), ("semantic", "INVALID_INPUT"),
    ("large_atomic", "OUTPUT_LIMIT"), ("failure", "EXECUTION_UNKNOWN"), ("wrong_ids", "EXECUTION_UNKNOWN")])
def test_sdk_fault_responses_and_connection_survives(mode, code, capfd, actual_sdk_frames):
    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "tests/p01_stdio_server.py"), mode])
        async with Client(params, read_timeout_seconds=15) as client:
            response = validate(await client.call_tool("service_status", {**ARGS, "trace_id": SECRET}))
            assert response["error_code"] == code
            assert len((await client.list_tools()).tools) == 12
    asyncio.run(exercise())
    captured = capfd.readouterr()
    assert SECRET not in captured.out + captured.err


def test_sdk_inprocess_mock_client_uses_same_contract():
    async def exercise():
        app = ServerScaffold()
        async with Client(app.sdk) as client:
            assert len((await client.list_tools()).tools) == 12
            response = validate(await client.call_tool("service_status", ARGS))
            assert response["error_code"] == "UNSUPPORTED"
    asyncio.run(exercise())


def test_id_admission_retains_cleanup_until_confirmed_before_exit(monkeypatch):
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCRequest
    events = []
    class Stream:
        async def receive(self):
            return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id="X" * 127, method="tools/list"))
    class Owner:
        attempts = 0
        async def close(self):
            self.attempts += 1
            events.append("cleanup")
            return self.attempts == 2
    def exit_process(code):
        events.append(("exit", code))
        raise SystemExit(code)
    monkeypatch.setattr(server_module, "os", SimpleNamespace(
        write=lambda fd, data: events.append(("diagnostic", fd, SECRET.encode() not in data)), _exit=exit_process))
    async def exercise():
        with pytest.raises(SystemExit) as exc:
            await server_module.AdmissionStream(Stream(), Owner()).receive()
        assert exc.value.code == 2
    asyncio.run(exercise())
    assert events == ["cleanup", "cleanup", ("diagnostic", 2, True), ("exit", 2)]
