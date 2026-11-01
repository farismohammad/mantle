import SwiftUI
import AppKit

// The Command Line Tools SDK exports a new @State macro without its Xcode
// plugin. Resolve the stable property-wrapper type explicitly for this build.
private typealias SessionViewState<Value> = SwiftUI.State<Value>

// Pi's documented RPC protocol: packages/coding-agent/docs/rpc.md,
// json.md, rpc-commands.md, and rpc-extension-ui.md in earendil-works/pi.
struct SessionTranscriptItem: Identifiable {
    let id: String
    var role: String
    var text: String
    var title: String = ""
    var detail: String = ""
    var running = false
    var isError = false
}

struct SessionDialog: Identifiable {
    let raw: JSON
    var id: String { raw["id"].string }
}

@MainActor final class NativeSession: ObservableObject {
    @Published var transcript: [SessionTranscriptItem] = []
    @Published var status = "Preparing your agent…"
    @Published var ready = false
    @Published var running = false
    @Published var ended = false
    @Published var error: String?
    @Published var diagnostics = ""
    @Published var sessionFile = ""
    @Published var sessionTitle = ""
    @Published var draft = ""
    @Published var dialogs: [SessionDialog] = []
    @Published var pendingMessages = 0
    @Published var usage = ""
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var errorOutput: FileHandle?
    private var buffer = Data()
    private var initialPrompt = ""
    private var activeAssistant: String?
    private var textBlocks: [Int: String] = [:]
    private var started = false
    private var closing = false
    private var pendingPrompts: [String: String] = [:]
    private let writer = DispatchQueue(label: "cloud.mantle.desktop.rpc-writer")

    func start(agent: MantleAgent, directory: String, prompt: String, includeContext: Bool,
               projectRoot: String, sessionID: String) {
        guard !started else { return }; started = true
        initialPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            guard FileManager.default.isExecutableFile(atPath: Engine.python.path) else {
                throw AppFailure(message: "The bundled Mantle engine is missing.")
            }
            var arguments = [Engine.bridge.path, "--cli", "agent", "run", agent.name]
            if !includeContext { arguments.append("--no-project-context") }
            if !projectRoot.isEmpty { arguments += ["--project-root", projectRoot] }
            if !sessionID.isEmpty { arguments += ["--session", sessionID] }
            arguments += ["--", "--mode", "rpc"]
            let child = Process(); child.executableURL = Engine.python
            child.arguments = arguments; child.environment = Engine.environment
            child.currentDirectoryURL = URL(fileURLWithPath: directory, isDirectory: true)
            let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
            child.standardInput = stdin; child.standardOutput = stdout; child.standardError = stderr
            input = stdin.fileHandleForWriting; output = stdout.fileHandleForReading; errorOutput = stderr.fileHandleForReading
            stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                if data.isEmpty { handle.readabilityHandler = nil; return }
                DispatchQueue.main.async { self?.consume(data) }
            }
            stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                if data.isEmpty { handle.readabilityHandler = nil; return }
                DispatchQueue.main.async {
                    guard let self, !data.isEmpty else { return }
                    let text = String(decoding: data, as: UTF8.self)
                    self.diagnostics = String((self.diagnostics + text).suffix(20000))
                    if !self.ready, let last = text.split(separator: "\n").last { self.status = String(last).prefixString(180) }
                }
            }
            child.terminationHandler = { [weak self] child in
                Task { @MainActor in
                    guard let self else { return }
                    self.ended = true; self.ready = false; self.running = false
                    if self.closing { self.status = "Session closed" }
                    else if child.terminationStatus != 0 {
                        self.status = "Session ended"
                        self.error = self.diagnostics.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            ? "The agent exited with status \(child.terminationStatus). Check Health and your provider connection."
                            : self.diagnostics
                    } else { self.status = "Session ended" }
                }
            }
            process = child
            try child.run()
            send("get_state", id: "initial-state")
        } catch {
            self.error = error.localizedDescription; status = "Couldn’t start this session"; ended = true
        }
    }

    func send(_ type: String, fields: [String: JSON] = [:], id: String? = nil) {
        guard let input, !ended, !closing else { return }
        var record = fields; record["type"] = .string(type); record["id"] = .string(id ?? UUID().uuidString)
        do {
            var data = try JSONEncoder().encode(JSON.object(record)); data.append(0x0A)
            writer.async { [weak self] in
                do { try input.write(contentsOf: data) }
                catch { Task { @MainActor in if self?.closing != true { self?.error = "The agent connection closed. Your saved session is available in Sessions." } } }
            }
        } catch { self.error = error.localizedDescription }
    }

    func submit() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ready, !text.isEmpty, !ended else { return }
        error = nil; draft = ""
        var fields: [String: JSON] = ["message": .string(text)]
        if running { fields["streamingBehavior"] = .string("steer"); status = "Instruction queued…" }
        else { running = true; status = "Working…" }
        let id = UUID().uuidString
        pendingPrompts[id] = text
        send("prompt", fields: fields, id: id)
    }

    func stop() {
        guard ready, running else { return }
        status = "Stopping…"
        for dialog in dialogs { answer(dialog, cancelled: true) }
        send("clear_queue")
        send("abort")
    }

    func close() {
        guard !closing else { return }
        for dialog in dialogs { answer(dialog, cancelled: true) }
        if running { send("clear_queue"); send("abort") }
        closing = true
        let handle = input
        writer.async { try? handle?.close() }
        input = nil
    }

    func answer(_ dialog: SessionDialog, value: String? = nil, confirmed: Bool? = nil, cancelled: Bool = false) {
        var fields: [String: JSON] = [:]
        if cancelled { fields["cancelled"] = .bool(true) }
        else if let confirmed { fields["confirmed"] = .bool(confirmed) }
        else { fields["value"] = .string(value ?? "") }
        send("extension_ui_response", fields: fields, id: dialog.id)
        dialogs.removeAll { $0.id == dialog.id }
    }

    // Data is split on LF bytes only, so split UTF-8 and U+2028/U+2029 survive.
    func consume(_ data: Data) {
        guard !data.isEmpty else { return }
        buffer.append(data)
        while let end = buffer.firstIndex(of: 0x0A) {
            var line = Data(buffer[..<end]); buffer.removeSubrange(...end)
            if line.last == 0x0D { line.removeLast() }
            guard !line.isEmpty else { continue }
            do { receive(try JSONDecoder().decode(JSON.self, from: line)) }
            catch {
                diagnostics = String((diagnostics + String(decoding: line, as: UTF8.self) + "\n").suffix(20000))
            }
        }
        if buffer.count > 16_000_000 {
            error = "The agent sent an oversized event. Open this session in Terminal to inspect the output."
            close()
        }
    }

    func receive(_ record: JSON) {
        switch record["type"].string {
        case "response": handleResponse(record)
        case "agent_start", "turn_start": running = true; status = "Working…"
        case "agent_end": send("get_state")
        case "agent_settled": running = false; status = record["aborted"].bool ? "Stopped" : "Ready"; send("get_state"); send("get_session_stats")
        case "message_start":
            let message = record["message"]
            if message["role"].string == "assistant" {
                let id = UUID().uuidString; activeAssistant = id; textBlocks = [:]
                transcript.append(SessionTranscriptItem(id: id, role: "assistant", text: "", running: true))
            } else if message["role"].string == "user" {
                transcript.append(SessionTranscriptItem(id: UUID().uuidString, role: "user", text: Self.messageText(message)))
            }
        case "message_update":
            let event = record["assistantMessageEvent"]
            if event["type"].string == "text_delta" {
                textBlocks[event["contentIndex"].int, default: ""] += event["delta"].string
                updateAssistant(text: textBlocks.keys.sorted().map { textBlocks[$0] ?? "" }.joined(separator: "\n\n"))
            } else if event["type"].string == "text_end" {
                textBlocks[event["contentIndex"].int] = event["content"].string
                updateAssistant(text: textBlocks.keys.sorted().map { textBlocks[$0] ?? "" }.joined(separator: "\n\n"))
            } else if event["type"].string.hasPrefix("thinking_") { status = "Thinking…" }
        case "message_end":
            let message = record["message"]
            if message["role"].string == "assistant" {
                let text = Self.messageText(message)
                updateAssistant(text: text)
                if let id = activeAssistant, let index = transcript.firstIndex(where: { $0.id == id }) { transcript[index].running = false }
                if !message["errorMessage"].string.isEmpty { error = message["errorMessage"].string }
                activeAssistant = nil
            }
        case "tool_execution_start":
            let id = record["toolCallId"].string
            transcript.append(SessionTranscriptItem(id: id.isEmpty ? UUID().uuidString : id, role: "tool", text: "", title: record["toolName"].string, detail: record["args"].pretty, running: true))
            status = "Using \(record["toolName"].string)…"
        case "tool_execution_update", "tool_execution_end":
            if let index = transcript.firstIndex(where: { $0.id == record["toolCallId"].string }) {
                let complete = record["type"].string == "tool_execution_end"
                let value = record[complete ? "result" : "partialResult"]
                transcript[index].text = Self.messageText(value).prefixString(30000)
                transcript[index].running = !complete; transcript[index].isError = record["isError"].bool
            }
        case "queue_update": pendingMessages = record["steering"].array.count + record["followUp"].array.count
        case "session_info_changed": sessionTitle = record["name"].string
        case "compaction_start", "auto_compaction_start": running = true; status = "Organizing session context…"
        case "compaction_end", "auto_compaction_end": status = "Working…"; if !record["errorMessage"].string.isEmpty { error = record["errorMessage"].string }
        case "auto_retry_start": status = "Retrying connection · Attempt \(record["attempt"].int)…"
        case "auto_retry_end": if !record["success"].bool { error = record["finalError"].string }
        case "extension_error": error = record["error"].string
        case "extension_ui_request":
            let method = record["method"].string
            if ["select", "confirm", "input", "editor"].contains(method) { dialogs.append(SessionDialog(raw: record)) }
            else if method == "set_editor_text" { draft = record["text"].string }
            else if method == "notify" {
                transcript.append(SessionTranscriptItem(id: UUID().uuidString, role: "notice", text: record["message"].string, isError: record["notifyType"].string == "error"))
            } else if method == "setStatus" { status = record["statusText"].string.isEmpty ? "Working…" : record["statusText"].string }
            else if method == "setTitle" { sessionTitle = record["title"].string }
            else if method == "setWidget", !record["widgetLines"].array.isEmpty {
                transcript.append(SessionTranscriptItem(id: UUID().uuidString, role: "notice", text: record["widgetLines"].array.map(\.string).joined(separator: "\n")))
            }
        default: break
        }
    }

    private func handleResponse(_ record: JSON) {
        let command = record["command"].string
        if !record["success"].bool {
            error = record["error"].string
            if command == "prompt" {
                running = false; status = "Ready"
                if let text = pendingPrompts.removeValue(forKey: record["id"].string) { draft = draft.isEmpty ? text : draft + "\n\n" + text }
            }
            return
        }
        let data = record["data"]
        switch command {
        case "get_state":
            sessionFile = data["sessionFile"].string; sessionTitle = data["sessionName"].string
            running = data["isStreaming"].bool || data["isCompacting"].bool || data["pendingMessageCount"].int > 0
            if !running { status = "Ready" }
            if record["id"].string == "initial-state" { status = "Opening the conversation…"; send("get_messages", id: "initial-messages") }
            else { ready = true }
        case "get_messages":
            if record["id"].string == "initial-messages" {
                ready = true; status = running ? "Working…" : "Ready"
                transcript = data["messages"].array.enumerated().compactMap { index, message in
                    let text = Self.messageText(message); let role = message["role"].string
                    guard !text.isEmpty else { return nil }
                    return SessionTranscriptItem(id: "history-\(index)", role: role == "toolResult" ? "tool" : role, text: text, title: message["toolName"].string)
                }
                if !initialPrompt.isEmpty { draft = initialPrompt; initialPrompt = ""; submit() }
            }
        case "prompt": pendingPrompts.removeValue(forKey: record["id"].string); if data["disposition"].string == "handled" { send("get_state") }
        case "abort": running = false; status = "Stopped"
        case "clear_queue":
            let restored = (data["steering"].array + data["followUp"].array).map(\.string).filter { !$0.isEmpty }.joined(separator: "\n\n")
            if !restored.isEmpty { draft = draft.isEmpty ? restored : draft + "\n\n" + restored }
        case "get_session_stats": usage = "\(data["tokens"]["total"].int.formatted()) tokens"
        default: break
        }
    }

    private func updateAssistant(text: String) {
        if let id = activeAssistant, let index = transcript.firstIndex(where: { $0.id == id }) { transcript[index].text = text }
    }

    static func messageText(_ message: JSON) -> String {
        let content = message["content"]
        if case .string(let text) = content { return text }
        return content.array.compactMap { block -> String? in
            if block["type"].string == "text" { return block["text"].string }
            if block["type"].string == "image" { return "[Image]" }
            return nil
        }.joined(separator: "\n\n")
    }
}

private extension String { func prefixString(_ limit: Int) -> String { String(prefix(limit)) } }

struct LiveSessionView: View {
    let agent: MantleAgent
    let directory: String
    let prompt: String
    let includeContext: Bool
    let projectRoot: String
    let sessionID: String
    @Environment(\.dismiss) private var dismiss
    @StateObject private var session = NativeSession()
    @SessionViewState private var confirmClose = false
    @SessionViewState private var showDiagnostics = false
    @FocusState private var composerFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                MantleMark(size: 36)
                VStack(alignment: .leading, spacing: 4) {
                    Text(session.sessionTitle.isEmpty ? agent.title : session.sessionTitle).font(.headline)
                    HStack(spacing: 5) { Text(agent.model); Text("·"); Text(URL(fileURLWithPath: directory).lastPathComponent) }.font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if session.running || !session.ready && !session.ended { ProgressView().controlSize(.small) }
                Text(session.status).font(.caption).foregroundStyle(.secondary).lineLimit(2).frame(maxWidth: 260, alignment: .trailing)
                Menu {
                    Button("Show Project in Finder") { NSWorkspace.shared.open(URL(fileURLWithPath: directory)) }
                    if !session.sessionFile.isEmpty { Button("Show Session File") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: session.sessionFile)]) } }
                    Button("Diagnostics…") { showDiagnostics = true }
                } label: { Image(systemName: "ellipsis.circle").font(.title3) }.menuStyle(.borderlessButton).frame(width: 25)
                Button("Done") { if session.running { confirmClose = true } else { session.close(); dismiss() } }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            conversation
            if let error = session.error, !error.isEmpty {
                HStack(alignment: .top) { Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange); Text(error).font(.caption).textSelection(.enabled).lineLimit(5); Spacer(); Button { session.error = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).help("Dismiss error") }.padding(14).background(Color.orange.opacity(0.08))
            }
            composer
        }
        .frame(minWidth: 760, idealWidth: 960, minHeight: 580, idealHeight: 720)
        .background(Color(nsColor: .windowBackgroundColor))
        .task { session.start(agent: agent, directory: directory, prompt: prompt, includeContext: includeContext, projectRoot: projectRoot, sessionID: sessionID); composerFocused = true }
        .onDisappear { session.close() }
        .interactiveDismissDisabled(session.running)
        .alert("Close this session?", isPresented: $confirmClose) {
            Button("Keep Working", role: .cancel) {}
            Button("Stop and Close") { session.close(); dismiss() }
        } message: { Text("The current run will stop. Your conversation remains saved and can be resumed from Sessions.") }
        .sheet(item: Binding(get: { session.dialogs.first }, set: { newValue in if newValue == nil, let current = session.dialogs.first { session.answer(current, cancelled: true) } })) { dialog in
            NativeSessionDialog(dialog: dialog) { value, confirmed, cancelled in session.answer(dialog, value: value, confirmed: confirmed, cancelled: cancelled) }
        }
        .sheet(isPresented: $showDiagnostics) {
            VStack(alignment: .leading, spacing: 18) { Text("Session Diagnostics").font(.title2.bold()); ScrollView { Text(session.diagnostics.isEmpty ? "No diagnostics have been reported." : session.diagnostics).font(.system(size: 11, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }; HStack { Spacer(); Button("Done") { showDiagnostics = false }.keyboardShortcut(.defaultAction) } }.padding(24).frame(width: 700, height: 450)
        }
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 24) {
                    if session.transcript.isEmpty {
                        VStack(spacing: 12) {
                            Image(systemName: agent.symbol).font(.system(size: 34, weight: .light)).foregroundStyle(Color.mantleAccent)
                            Text(session.ready ? "What would you like to work on?" : "Getting ready to work.").font(.title2.weight(.medium))
                            Text(session.ready ? "Your agent has its own instructions, skills, and tools, with this project as its working folder." : "Mantle is preparing the agent’s runtime and connections.").font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 440)
                        }.frame(maxWidth: .infinity).padding(.vertical, 90)
                    }
                    ForEach(session.transcript) { item in SessionTranscriptRow(item: item, agentTitle: agent.title) }
                    Color.clear.frame(height: 1).id("latest")
                }.padding(.horizontal, 38).padding(.vertical, 28).frame(maxWidth: 960).frame(maxWidth: .infinity)
            }
            .onChange(of: session.transcript.count) { _, _ in proxy.scrollTo("latest", anchor: .bottom) }
            .onChange(of: session.transcript.last?.text) { _, _ in proxy.scrollTo("latest", anchor: .bottom) }
        }
    }

    private var composer: some View {
        VStack(spacing: 10) {
            Divider()
            HStack(alignment: .bottom, spacing: 12) {
                TextField(session.running ? "Add an instruction…" : "Message \(agent.title)…", text: $session.draft, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(2...6).font(.system(size: 14)).focused($composerFocused)
                    .onSubmit { session.submit() }.disabled(!session.ready || session.ended)
                if session.running { Button { session.stop() } label: { Image(systemName: "stop.fill").frame(width: 20, height: 20) }.buttonStyle(.bordered).help("Stop the current run") }
                Button { session.submit() } label: { Image(systemName: "arrow.up").font(.system(size: 15, weight: .semibold)).frame(width: 22, height: 22) }.buttonStyle(.borderedProminent).help(session.running ? "Queue an instruction" : "Send message").disabled(!session.ready || session.ended || session.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty).keyboardShortcut(.return, modifiers: .command)
            }.padding(16).background(.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 16)).overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(.primary.opacity(0.08)))
            HStack { Text(session.pendingMessages > 0 ? "\(session.pendingMessages) instruction(s) queued" : "\(agent.provider) · \(session.usage.isEmpty ? "Saved on this Mac" : session.usage)"); Spacer(); Text("⌘ Return to send") }.font(.caption2).foregroundStyle(.secondary)
        }.padding(.horizontal, 28).padding(.bottom, 20)
    }
}

private struct SessionTranscriptRow: View {
    let item: SessionTranscriptItem
    let agentTitle: String
    var body: some View {
        if item.role == "tool" {
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 10) {
                    if !item.detail.isEmpty { Text(item.detail).foregroundStyle(.secondary) }
                    Text(item.text.isEmpty ? (item.running ? "Running…" : "Completed") : item.text)
                }.font(.system(size: 11, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 8)
            } label: { HStack(spacing: 8) { if item.running { ProgressView().controlSize(.mini) } else { Image(systemName: item.isError ? "exclamationmark.circle" : "checkmark.circle").foregroundStyle(item.isError ? .orange : .secondary) }; Text(item.title.isEmpty ? "Tool result" : item.title).font(.caption.weight(.medium)) } }.padding(12).background(.primary.opacity(0.03), in: RoundedRectangle(cornerRadius: 10))
        } else if item.role == "notice" {
            Label(item.text, systemImage: item.isError ? "exclamationmark.circle" : "info.circle").font(.caption).foregroundStyle(item.isError ? .orange : .secondary).textSelection(.enabled)
        } else if !item.text.isEmpty || item.running {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) { Image(systemName: item.role == "user" ? "person.crop.circle.fill" : "square.stack.3d.up.fill").foregroundStyle(item.role == "user" ? .secondary : Color.mantleAccent); Text(item.role == "user" ? "You" : agentTitle).font(.callout.weight(.semibold)); if item.running { ProgressView().controlSize(.mini) } }
                if !item.text.isEmpty { Text(item.text).font(.system(size: 14)).lineSpacing(5).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                else { Text("Thinking…").font(.callout).foregroundStyle(.secondary) }
            }.padding(item.role == "user" ? 16 : 0).frame(maxWidth: .infinity, alignment: .leading).background(item.role == "user" ? Color.primary.opacity(0.035) : .clear, in: RoundedRectangle(cornerRadius: 12))
        }
    }
}

private struct NativeSessionDialog: View {
    let dialog: SessionDialog
    let answer: (String?, Bool?, Bool) -> Void
    @SessionViewState private var input = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Label("Agent Request", systemImage: "hand.raised").font(.caption).foregroundStyle(.secondary)
            Text(dialog.raw["title"].string).font(.title2.weight(.semibold))
            if !dialog.raw["message"].string.isEmpty { Text(dialog.raw["message"].string).textSelection(.enabled) }
            if dialog.raw["method"].string == "select" {
                VStack(alignment: .leading, spacing: 8) { ForEach(dialog.raw["options"].array.map(\.string), id: \.self) { option in Button(option) { answer(option, nil, false) }.frame(maxWidth: .infinity, alignment: .leading) } }
            } else if dialog.raw["method"].string == "editor" {
                TextEditor(text: $input).font(.system(size: 13, design: .monospaced)).frame(height: 180).border(.secondary.opacity(0.2))
            } else if dialog.raw["method"].string == "input" {
                TextField(dialog.raw["placeholder"].string, text: $input).textFieldStyle(.roundedBorder)
            }
            HStack {
                Button("Cancel") { answer(nil, nil, true) }.keyboardShortcut(.cancelAction)
                Spacer()
                if dialog.raw["method"].string == "confirm" { Button("Decline") { answer(nil, false, false) }; Button("Confirm") { answer(nil, true, false) }.buttonStyle(.borderedProminent) }
                else if dialog.raw["method"].string != "select" { Button("Continue") { answer(input, nil, false) }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction) }
            }
        }.padding(28).frame(width: 520).onAppear { input = dialog.raw["prefill"].string }.interactiveDismissDisabled()
    }
}
