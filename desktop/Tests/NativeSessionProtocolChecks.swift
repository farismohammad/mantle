import Foundation

// Standalone protocol checks. Compile with Core.swift and LiveSessionView.swift;
// no real agent, provider, subprocess, or project is started.
@main struct NativeSessionProtocolChecks {
    @MainActor static func main() throws {
        let session = NativeSession()
        func record(_ text: String) throws { session.receive(try JSONDecoder().decode(JSON.self, from: Data(text.utf8))) }
        try record(#"{"type":"response","id":"initial-state","command":"get_state","success":true,"data":{"sessionId":"test","sessionFile":"/example/session.jsonl","isStreaming":false}}"#)
        precondition(!session.ready && session.sessionFile == "/example/session.jsonl")
        try record(#"{"type":"response","id":"initial-messages","command":"get_messages","success":true,"data":{"messages":[]}}"#)
        precondition(session.ready)
        try record(#"{"type":"agent_start"}"#)
        try record(#"{"type":"message_start","message":{"role":"assistant","content":[]}}"#)
        let unicode = "hello\u{2028}world 🦕"
        var bytes = try JSONEncoder().encode(JSON.object([
            "type": .string("message_update"),
            "assistantMessageEvent": .object(["type": .string("text_delta"), "contentIndex": .number(0), "delta": .string(unicode)])
        ]))
        bytes.append(0x0A)
        // Every possible byte boundary can be split, including the emoji bytes.
        for byte in bytes { session.consume(Data([byte])) }
        precondition(session.transcript.last?.text == unicode)
        try record(#"{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Final authoritative answer."}]}}"#)
        precondition(session.transcript.last?.text == "Final authoritative answer.")
        precondition(session.transcript.last?.running == false)
        try record(#"{"type":"tool_execution_start","toolCallId":"t1","toolName":"read","args":{"path":"README.md"}}"#)
        precondition(session.transcript.last?.running == true)
        try record(#"{"type":"tool_execution_end","toolCallId":"t1","toolName":"read","result":{"content":[{"type":"text","text":"File content"}]},"isError":false}"#)
        precondition(session.transcript.last?.text == "File content")
        precondition(session.transcript.last?.running == false)
        try record(#"{"type":"extension_ui_request","id":"approval-1","method":"confirm","title":"Allow this change?","message":"Review first."}"#)
        precondition(session.dialogs.count == 1 && session.dialogs[0].id == "approval-1")
        session.answer(session.dialogs[0], confirmed: false)
        precondition(session.dialogs.isEmpty)
        try record(#"{"type":"agent_end","messages":[],"willRetry":false}"#)
        precondition(session.running, "agent_end must not prematurely mark a session settled")
        try record(#"{"type":"agent_settled","aborted":false}"#)
        precondition(!session.running && session.status == "Ready")
        try record(#"{"type":"response","command":"prompt","success":false,"error":"Provider unavailable"}"#)
        precondition(session.error == "Provider unavailable")
        try record(#"{"type":"response","command":"clear_queue","success":true,"data":{"steering":["Keep this instruction"],"followUp":[]}}"#)
        precondition(session.draft == "Keep this instruction")
        print("Native RPC checks passed: UTF-8/JSONL framing, final messages, tools, dialogs, lifecycle, failures, and queued instructions.")
    }
}
