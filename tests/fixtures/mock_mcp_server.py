"""A tiny stdio MCP server for tests. Mode is argv[1]:

new-spec        answers server/discover (2026-07-28), no initialize
old-spec        -32601 on server/discover, needs initialize first (2025-06-18)
input-required  new-spec, but tools/call "ask" returns input_required once
crash           new-spec, exits on tools/call
hang            new-spec, never answers tools/call
"""
import json
import os
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "new-spec"
TOOLS = [{"name": f"t{i}", "description": f"tool {i}", "inputSchema": {"type": "object"}}
         for i in range(3)]


def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    initialized = False
    sys.stderr.write(f"mock server up ({MODE})\n")
    sys.stderr.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if mid is None:
            if method == "notifications/initialized":
                initialized = True
            continue
        if MODE == "old-spec":
            if method == "server/discover":
                reply(mid, error={"code": -32601, "message": "Method not found"})
                continue
            if method == "initialize":
                reply(mid, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                            "serverInfo": {"name": "old", "version": "1"}})
                continue
            if not initialized:
                reply(mid, error={"code": -32002, "message": "not initialized"})
                continue
        elif "io.modelcontextprotocol/protocolVersion" not in (params.get("_meta") or {}):
            reply(mid, error={"code": -32602, "message": "missing _meta"})
            continue
        if method == "server/discover":
            reply(mid, {"resultType": "complete", "supportedVersions": ["2026-07-28"],
                        "capabilities": {"tools": {}}, "instructions": "be nice",
                        "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "new", "version": "2"}}})
        elif method == "tools/list":
            start = int(params.get("cursor") or 0)
            page = {"tools": TOOLS[start:start + 2]}
            if start + 2 < len(TOOLS):
                page["nextCursor"] = str(start + 2)
            reply(mid, page)
        elif method == "tools/call":
            if MODE == "crash":
                sys.stderr.write("boom\n")
                sys.stderr.flush()
                sys.exit(3)
            if MODE == "hang":
                continue
            name, args = params.get("name"), params.get("arguments") or {}
            if name == "env":  # what a configured env var reached the child as
                reply(mid, {"content": [{"type": "text", "text": os.environ.get("MCP_TEST", "")}]})
            elif name == "fail":
                reply(mid, error={"code": -32602, "message": "bad arguments"})
            elif name == "ask" and "inputResponses" not in params:
                reply(mid, {"resultType": "input_required", "requestState": "s1",
                            "inputRequests": {"who": {"method": "elicitation/create",
                                                      "params": {"message": "Name?", "requestedSchema": {
                                                          "type": "object", "required": ["name"],
                                                          "properties": {"name": {"type": "string"}}}}}}})
            elif name == "ask":
                who = params["inputResponses"]["who"]["content"]["name"]
                reply(mid, {"content": [{"type": "text",
                                         "text": f"hi {who} state={params.get('requestState')}"}]})
            else:
                reply(mid, {"resultType": "complete", "isError": name == "bad",
                            "content": [{"type": "text", "text": f"echo {args}"},
                                        {"type": "image", "data": "A" * 16384, "mimeType": "image/png"}]})


if __name__ == "__main__":
    main()
