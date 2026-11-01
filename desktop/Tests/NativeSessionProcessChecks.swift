import Foundation

// Run only with MANTLE_DESKTOP_RESOURCES pointing at a new fixture directory
// containing fixtures/rpc_engine.py as bridge/mantle_bridge.py.
@main struct NativeSessionProcessChecks {
    @MainActor static func main() async throws {
        guard let directory = ProcessInfo.processInfo.environment["MANTLE_TEST_PROJECT"],
              ProcessInfo.processInfo.environment["MANTLE_DESKTOP_RESOURCES"] != nil else {
            fatalError("An explicit offline fixture resource folder and project are required.")
        }
        let session = NativeSession()
        let agent = MantleAgent(raw: .object(["name": .string("fixture"), "model": .string("offline-fixture"), "provider": .string("fixture")]))
        session.start(agent: agent, directory: directory, prompt: "Fixture request", includeContext: false,
                      projectRoot: directory, sessionID: "fixture-session")
        try await until("startup, prompt, and extension request") { session.dialogs.count == 1 }
        precondition(session.ready && session.running)
        precondition(session.transcript.first?.text == "Saved fixture history")
        precondition(session.transcript.contains { $0.text == "Streamed Unicode 🦕\u{2028}text" })
        precondition(session.transcript.contains { $0.role == "tool" && $0.text == "Simulated tool output" && !$0.running })
        session.answer(session.dialogs[0], confirmed: false)
        try await until("legacy agent_end state query") { !session.running }
        precondition(session.transcript.contains { $0.text == "Authoritative fixture result" && !$0.running })
        session.draft = "Wait for stop"
        session.submit()
        try await until("second prompt") { session.transcript.contains { $0.role == "user" && $0.text == "Wait for stop" } }
        session.stop()
        try await until("abort response") { !session.running }
        session.close()
        try await until("graceful EOF shutdown") { session.ended }
        precondition(session.error == nil, session.error ?? "")
        print("Native process smoke passed: startup/history, real pipes, prompt args, Unicode stream, tool events, extension reply, legacy idle fallback, abort, and graceful EOF.")
    }

    @MainActor static func until(_ label: String, condition: () -> Bool) async throws {
        for _ in 0..<400 {
            if condition() { return }
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        throw AppFailure(message: "Offline process check timed out: \(label)")
    }
}
