import inspect
import mock_mcp_server as s
from mcp.server.mcpserver.utilities.func_metadata import func_metadata

names = [
    "query_journal",
    "search_log",
    "service_status",
    "list_failed_units",
    "disk_usage",
    "listening_ports",
    "check_config",
    "memory_oom_events",
    "failed_logins",
    "detect_anomaly",
    "verify_fix",
    "restart_service",
]

for name in names:
    fn = getattr(s, name)
    meta = func_metadata(fn)

    print(f"\n=== {name} ===")
    print("SIGNATURE:", inspect.signature(fn))
    print("OUTPUT_SCHEMA:", meta.output_schema)
    print("INPUT_SCHEMA:", meta.arg_model.model_json_schema())
