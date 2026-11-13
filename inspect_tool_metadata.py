"""Inspect authoritative contracts without deriving schemas from Python signatures."""
import json
import mock_mcp_server as server

for name, (_, input_schema, output_schema, _, _) in server.TOOLS.items():
    print(json.dumps({"name": name, "inputSchema": input_schema,
                      "outputSchema": output_schema}, ensure_ascii=False, indent=2))
