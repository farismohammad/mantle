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
        session.error = nil; session.draft = ""
        // Older Pi versions: a failed clear_queue only matters if something was queued.
        try record(#"{"type":"response","command":"clear_queue","success":false,"error":"Unknown command: clear_queue"}"#)
        precondition(session.error == nil)
        try record(#"{"type":"queue_update","steering":["Queued"],"followUp":[]}"#)
        try record(#"{"type":"response","command":"clear_queue","success":false,"error":"Unknown command: clear_queue"}"#)
        precondition(session.error?.contains("can’t cancel queued instructions") == true)
        try record(#"{"type":"queue_update","steering":[],"followUp":[]}"#)
        session.error = nil
        // A missing key points to Connections rather than Pi's terminal /login.
        try record(#"{"type":"response","command":"prompt","success":false,"error":"No API key found for openai.\n\nUse /login to log into a provider."}"#)
        precondition(session.needsCredentials && session.error?.contains("/login") == false && session.error?.contains("Connections") == true)
        session.error = nil; session.draft = ""; session.needsCredentials = false
        // A provider failure is reported, but an abort the user asked for isn't.
        try record(#"{"type":"message_start","message":{"role":"assistant","content":[]}}"#)
        try record(#"{"type":"message_end","message":{"role":"assistant","content":[],"stopReason":"error","errorMessage":"Provider unavailable"}}"#)
        precondition(session.error == "Provider unavailable")
        session.error = nil
        try record(#"{"type":"message_start","message":{"role":"assistant","content":[]}}"#)
        try record(#"{"type":"message_end","message":{"role":"assistant","content":[],"stopReason":"aborted","errorMessage":"Request was aborted"}}"#)
        precondition(session.error == nil)
        try record(#"{"type":"agent_start"}"#)
        session.stop()
        try record(#"{"type":"message_start","message":{"role":"assistant","content":[]}}"#)
        try record(#"{"type":"message_end","message":{"role":"assistant","content":[],"errorMessage":"Request was aborted"}}"#)
        try record(#"{"type":"agent_settled","aborted":false}"#)
        precondition(session.error == nil && session.status == "Stopped")
        try record(#"{"type":"response","command":"get_state","success":true,"data":{"isStreaming":false}}"#)
        precondition(session.status == "Stopped")
        // The first message must never be saved with a window, or a restored window would send it again.
        let launch = SessionLaunch(agentName: "fixture", directory: "/example")
        let saved = String(decoding: try JSONEncoder().encode(launch), as: UTF8.self)
        precondition(!saved.contains("prompt"), saved)
        let model = AppModel()
        precondition(!model.openedLaunches.contains(launch.launchID), "An unopened launch must count as restored")
        model.prepareSession(launch, firstMessage: "Once only")
        precondition(model.openedLaunches.contains(launch.launchID))
        precondition(model.takeFirstMessage(launch.launchID) == "Once only" && model.takeFirstMessage(launch.launchID).isEmpty)
        print("Native RPC checks passed: UTF-8/JSONL framing, final messages, tools, dialogs, lifecycle, failures, queued instructions, stops, missing keys, and restored windows.")
    }
}
