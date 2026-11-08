"""Offline fake Pi RPC process for the native pipe/lifecycle smoke test.

This fixture never imports Mantle, reads credentials, calls a provider, installs
a runtime, runs tools, or changes project files.
"""
import json
import os
import sys

expected = ["--cli", "agent", "run", "fixture", "--no-project-context",
            "--project-root", os.getcwd(), "--session", "fixture-session", "--", "--mode", "rpc"]
assert sys.argv[1:] == expected, (sys.argv[1:], expected)
running = False
awaiting_confirmation = False


def emit(record):
    data = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    for offset in range(0, len(data), 7):
        os.write(sys.stdout.fileno(), data[offset:offset + 7])


def response(command, data=None):
    record = {"type": "response", "command": command["type"], "id": command.get("id"), "success": True}
    if data is not None:
        record["data"] = data
    emit(record)


sys.stderr.write("Offline fixture engine ready.\n")
sys.stderr.flush()
for line in sys.stdin.buffer:
    command = json.loads(line)
    kind = command["type"]
    if kind == "get_state":
        response(command, {"isStreaming": running, "isCompacting": False, "pendingMessageCount": 0,
                           "sessionFile": "/fixture/session.jsonl", "sessionId": "fixture-session"})
    elif kind == "get_messages":
        response(command, {"messages": [{"role": "user", "content": "Saved fixture history"},
                                       {"role": "assistant", "content": [{"type": "text", "text": "Existing response"}]}]})
    elif kind == "prompt":
        running = True
        response(command, {"disposition": "started"})
        emit({"type": "agent_start"})
        emit({"type": "message_start", "message": {"role": "user", "content": command["message"]}})
        if command["message"] == "Wait for stop":
            continue
        emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
        emit({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                                      "delta": "Streamed Unicode 🦕\u2028text"}})
        emit({"type": "tool_execution_start", "toolCallId": "fixture-tool", "toolName": "fixture-read", "args": {"path": "README.md"}})
        emit({"type": "tool_execution_end", "toolCallId": "fixture-tool", "toolName": "fixture-read",
              "result": {"content": [{"type": "text", "text": "Simulated tool output"}]}, "isError": False})
        awaiting_confirmation = True
        emit({"type": "extension_ui_request", "id": "fixture-confirm", "method": "confirm", "title": "Fixture confirmation"})
    elif kind == "extension_ui_response":
        assert awaiting_confirmation and command["id"] == "fixture-confirm"
        assert command.get("confirmed") is False
        awaiting_confirmation = False
        emit({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Authoritative fixture result"}]}})
        running = False
        # Legacy Pi compatibility: no agent_settled event; the client queries get_state.
        emit({"type": "agent_end", "messages": []})
    elif kind == "clear_queue":
        response(command, {"steering": [], "followUp": []})
    elif kind == "abort":
        running = False
        response(command)
        emit({"type": "agent_settled", "aborted": True})
    elif kind == "get_available_models":
        response(command, {"models": [{"provider": "fixture", "id": "offline-fixture", "name": "Offline Fixture"}]})
    elif kind == "get_available_thinking_levels":
        response(command, {"levels": ["off"]})
    elif kind == "get_session_stats":
        response(command, {"tokens": {"total": 123}})
    else:
        raise AssertionError("Unexpected test command: " + kind)
# Normal EOF is the only shutdown mechanism used by this test.
