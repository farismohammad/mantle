import SwiftUI
import AppKit

struct AgentEditor: View {
    @EnvironmentObject private var app: AppModel
    @Environment(\.dismiss) private var dismiss
    let agent: MantleAgent?
    @State private var name = ""
    @State private var purpose = ""
    @State private var provider = "openai"
    @State private var model = "gpt-5.6"
    @State private var runtime = "latest"
    @State private var instructions = ""
    @State private var filesystem = "all"
    @State private var shell = "all"
    @State private var yaml = ""
    @State private var base: [String:JSON] = [:]
    @State private var mode = "Details"
    @State private var previousMode = "Details"
    @State private var saving = false
    @State private var switching = false
    @State private var validation: String?
    @State private var valid = false
    @State private var discard = false
    @State private var initialized = false
    @State private var pendingSkills: [PendingSkill] = []
    @State private var skillSource = ""
    @State private var skillName = ""
    @State private var status: String?
    var body: some View {
        VStack(spacing:0) {
            HStack(alignment:.top) { PageHeader(title:agent == nil ? "Create an Agent" : "Edit \(agent!.title)",subtitle:"Give it a clear purpose and just what it needs.",symbol:"square.stack.3d.up"); Picker("Editor mode",selection:$mode) { Text("Details").tag("Details"); Text("YAML").tag("YAML") }.pickerStyle(.segmented).labelsHidden().frame(width:160).disabled(saving || switching) }.padding(28)
            Divider()
            if mode == "Details" {
                Form {
                    Section("Identity") {
                        FormField(title:"Name",text:Binding(get:{name},set:{ name = agent == nil ? AgentEditor.normalizedName($0) : $0 }),prompt:"e.g. code-reviewer").disabled(agent != nil).autocorrectionDisabled()
                        if let problem = nameProblem { Text(problem).font(.caption).foregroundStyle(.red) }
                        else { Text("Lowercase letters, numbers, hyphens, and underscores (up to 64). Capitals become lowercase and spaces become hyphens as you type.").font(.caption).foregroundStyle(.secondary) }
                        FormField(title:"Purpose",text:$purpose,prompt:"What is this agent here to do?",multiline:true)
                    }
                    Section("Model") {
                        Picker("Provider",selection:Binding(get:{provider},set:{ value in provider = value; if let selected = app.providers.first(where:{$0["id"].string == value}), !selected["default_model"].string.isEmpty { model = selected["default_model"].string } })) { ForEach(app.providers,id: \.self) { p in Text(p["name"].string).tag(p["id"].string) }; if !app.providers.contains(where:{$0["id"].string == provider}) { Text(provider).tag(provider) } }
                        FormField(title:"Model",text:$model)
                        FormField(title:"Pi version",text:$runtime,prompt:"latest")
                    }
                    Section("Instructions") {
                        Text("Describe how this agent should work, what it should pay attention to, and what a good result looks like.").font(.caption).foregroundStyle(.secondary)
                        TextEditor(text:$instructions).font(.system(size:13)).frame(minHeight:130).scrollContentBackground(.hidden).padding(8).background(.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:8)).accessibilityLabel("System instructions")
                    }
                    if agent == nil {
                        Section("Skills") {
                            Text("Optional. Skills are installed right after the agent is created. Use a Git URL or a local folder; if the source holds several skills, name the one you want. A source left in the field below is added too.").font(.caption).foregroundStyle(.secondary)
                            ForEach(pendingSkills) { skill in
                                HStack { Image(systemName:"sparkles").foregroundStyle(Color.mantleAccent); VStack(alignment:.leading,spacing:2) { Text(skill.name.isEmpty ? "Skill from source" : skill.name).font(.callout.weight(.medium)); Text(skill.source).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle) }; Spacer(); Button { pendingSkills.removeAll { $0.id == skill.id } } label: { Image(systemName:"minus.circle") }.buttonStyle(.borderless).help("Remove") }
                            }
                            FormField(title:"Source",text:$skillSource,prompt:"https://github.com/owner/skills or /path/to/skill")
                            FormField(title:"Skill name",text:$skillName,prompt:"Optional")
                            HStack { Button("Choose Folder…") { pickSkillFolder() }; Spacer(); Button { addPendingSkill() } label: { Label("Add Skill",systemImage:"plus") }.disabled(skillSource.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty) }
                            Text("Only install skills from sources you trust.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Section("Available tools") {
                        Picker("Files",selection:$filesystem) { Text("Read and write").tag("all"); Text("Read only").tag("read"); Text("No file tools").tag("none") }
                        Toggle("Shell access",isOn:Binding(get:{shell == "all"},set:{shell = $0 ? "all" : "none"}))
                        Text("Tool choices are not an operating-system sandbox. Configure MCP servers, secret names, and resource declarations in YAML.").font(.caption).foregroundStyle(.secondary)
                    }
                }.formStyle(.grouped).multilineTextAlignment(.leading)
            } else {
                VStack(alignment:.leading,spacing:12) {
                    HStack { Text("The complete Mantle configuration, including MCP and resources.").font(.caption).foregroundStyle(.secondary); Spacer(); Button("Format") { Task { await format() } }.disabled(saving) }
                    LeftAlignedTextView(text:$yaml,accessibilityTitle:"Agent YAML configuration",font:.monospacedSystemFont(ofSize:12,weight:.regular),tinted:false).padding(12).background(.primary.opacity(0.035),in:RoundedRectangle(cornerRadius:10)).accessibilityLabel("Agent YAML configuration")
                    Text("Resource declarations describe installs. Use the Skills and Packages tabs to install or remove them.").font(.caption).foregroundStyle(.secondary)
                }.padding(24)
            }
            Divider()
            VStack(alignment:.leading,spacing:12) {
                if let validation { ScrollView { Text(validation).font(.caption).foregroundStyle(valid ? .green : .red).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading) }.frame(maxHeight:80) }
                HStack { if saving || switching { ProgressView().controlSize(.small) }; if let status { Text(status).font(.caption).foregroundStyle(.secondary) }; Button("Validate") { Task { await validate() } }.disabled(saving || switching); Spacer(); Button("Cancel") { discard = true }.keyboardShortcut(.cancelAction).disabled(saving); Button(agent == nil ? "Create Agent" : "Save Changes") { Task { await save() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(saving || switching || (mode == "Details" && (name.isEmpty || nameProblem != nil))) }
            }.padding(22)
        }.frame(minWidth:680,idealWidth:750,maxWidth:.infinity,minHeight:620,idealHeight:800,maxHeight:.infinity).task { initialize() }
        .onChange(of:mode) { old,new in guard initialized, !switching, old != new else { return }; Task { await switchMode(from:old,to:new) } }
        .alert("Discard these changes?",isPresented:$discard) { Button("Keep Editing",role:.cancel) {}; Button("Discard Changes",role:.destructive) { dismiss() } } message: { Text("Your saved agent will remain as it is.") }
    }
    /// Mirrors AGENT_NAME_PATTERN (^[a-z0-9_][a-z0-9_-]*$, max 64) in src/mantle/models.py.
    static func normalizedName(_ raw: String) -> String {
        let allowed = Set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        var result = String(raw.lowercased().map { $0 == " " ? "-" : $0 }.filter { allowed.contains($0) })
        while result.hasPrefix("-") { result.removeFirst() }
        return String(result.prefix(64))
    }
    private var nameProblem: String? {
        guard agent == nil, !name.isEmpty else { return nil }
        if name.range(of:"^[a-z0-9_][a-z0-9_-]*$",options:.regularExpression) == nil || name.count > 64 { return "Use only lowercase letters, numbers, hyphens, and underscores, not starting with a hyphen (up to 64)." }
        if app.agents.contains(where:{ $0.name == name }) { return "An agent named \(name) already exists." }
        return nil
    }
    private func addPendingSkill() { let source = skillSource.trimmingCharacters(in:.whitespacesAndNewlines); guard !source.isEmpty else { return }; pendingSkills.append(PendingSkill(source:source,name:skillName.trimmingCharacters(in:.whitespacesAndNewlines))); skillSource = ""; skillName = "" }
    private func pickSkillFolder() { let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false; panel.prompt = "Use This Folder"; if panel.runModal() == .OK, let path = panel.url?.path { skillSource = path } }
    private func installPendingSkills(for agentName: String) async -> [String] {
        var failures: [String] = []
        for skill in pendingSkills {
            var p: [String:JSON] = ["agent":.string(agentName),"source":.string(skill.source)]
            if !skill.name.isEmpty { p["skill"] = .string(skill.name) }
            do { _ = try await app.request("skills.add",p) } catch { failures.append("\(skill.name.isEmpty ? skill.source : skill.name): \(error.localizedDescription)") }
        }
        return failures
    }
    private func initialize() { guard !initialized else { return }; if let agent { base = agent.config.object; yaml = agent.yaml; populate(agent.config) } else { if let signed = app.credentials.first, let p = app.providers.first(where:{$0["id"].string == signed["provider"].string}) { provider = p["id"].string; if !p["default_model"].string.isEmpty { model = p["default_model"].string } }; base = ["mantle":.string("0.1"),"resources":.object(["skills":.array([]),"packages":.array([])]),"mcp":.object(["servers":.object([:])]),"secrets":.array([])] }; initialized = true }
    private func populate(_ config: JSON) { name = config["agent"]["name"].string; purpose = config["agent"]["description"].string; provider = config["model"]["provider"].string; model = config["model"]["name"].string; runtime = config["runtime"]["version"].string; instructions = config["system_prompt"].string; filesystem = config["permissions"]["filesystem"].string; shell = config["permissions"]["shell"].string }
    private var draft: String { var value = base; value["agent"] = .object(["name":.string(name),"description":.string(purpose)]); value["model"] = .object(["provider":.string(provider),"name":.string(model)]); value["runtime"] = .object(["name":.string("pi"),"version":.string(runtime)]); value["system_prompt"] = instructions.isEmpty ? .null : .string(instructions); value["permissions"] = .object(["filesystem":.string(filesystem),"shell":.string(shell)]); return JSON.object(value).pretty }
    private var text: String { mode == "YAML" ? yaml : draft }
    private func parsed(_ text: String) async throws -> JSON { var params:[String:JSON] = ["yaml":.string(text)]; if let agent { params["name"] = .string(agent.name) }; return try await app.request("agent.format",params) }
    private func switchMode(from old:String,to new:String) async { switching = true; defer { switching = false }; do { let value = try await parsed(old == "Details" ? draft : yaml); if new == "YAML" { yaml = value["yaml"].string } else { base = value["config"].object; populate(value["config"]) }; validation = nil } catch { if new == "YAML" { yaml = draft; validation = "Complete the configuration before saving."; valid = false } else { mode = old; validation = error.localizedDescription; valid = false } } }
    private func format() async { do { yaml = try await parsed(yaml)["yaml"].string; validation = "Configuration formatted."; valid = true } catch { validation = error.localizedDescription; valid = false } }
    private func validate() async { do { _ = try await parsed(text); validation = "Configuration is valid."; valid = true } catch { validation = error.localizedDescription; valid = false } }
    private func save() async { saving = true; defer { saving = false; status = nil }; if agent == nil, !skillSource.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty { addPendingSkill() }; do { let value = try await parsed(text); var params:[String:JSON] = ["name":value["config"]["agent"]["name"],"yaml":.string(mode == "YAML" ? yaml : value["yaml"].string),"confirmed":.bool(true),"preserveComments":.bool(mode == "Details")]; if let agent { params["expectedDigest"] = agent.raw["digest"] }; let result = try await app.request(agent == nil ? "agent.create" : "agent.save",params); var skillFailures: [String] = []; if agent == nil, !pendingSkills.isEmpty { status = "Installing \(pendingSkills.count) skill\(pendingSkills.count == 1 ? "" : "s")…"; skillFailures = await installPendingSkills(for:result["name"].string) }; await app.refresh(); app.selection = result["name"].string; app.destination = .agents; if !skillFailures.isEmpty { app.error = "Agent created, but some skills could not be installed. Add them from the agent’s Skills tab.\n" + skillFailures.joined(separator:"\n") } else { app.notice = agent == nil ? (pendingSkills.isEmpty ? "Agent created. Add skills or start a session when you’re ready." : "Agent created with \(pendingSkills.count) skill\(pendingSkills.count == 1 ? "" : "s").") : "Changes saved. A copy of the previous configuration is in Backups." }; dismiss() } catch { validation = error.localizedDescription; valid = false } }
}

/// A full-width text field with its label above. It is backed directly by an AppKit NSTextField with
/// left alignment and left-to-right writing direction, because SwiftUI text fields inside a grouped
/// macOS Form are drawn right-aligned, so typing appeared to run from right to left.
struct FormField: View {
    let title: String
    @Binding var text: String
    var prompt: String = ""
    var multiline = false
    var body: some View {
        VStack(alignment:.leading,spacing:6) {
            Text(title).font(.callout.weight(.medium))
            if multiline {
                LeftAlignedTextView(text:$text,placeholder:prompt,accessibilityTitle:title)
                    .frame(maxWidth:.infinity,minHeight:64,maxHeight:64,alignment:.leading)
                    .overlay(RoundedRectangle(cornerRadius:6).strokeBorder(Color.primary.opacity(0.18)))
            } else {
                LeftAlignedTextField(text:$text,placeholder:prompt,multiline:false,accessibilityTitle:title)
                    .frame(maxWidth:.infinity,minHeight:24,maxHeight:24,alignment:.leading)
            }
        }.frame(maxWidth:.infinity,alignment:.leading)
    }
}

struct LeftAlignedTextField: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String
    var multiline: Bool
    var accessibilityTitle: String
    @Environment(\.isEnabled) private var isEnabled

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSTextField {
        let field = NSTextField(string: text)
        field.isEditable = true
        field.isSelectable = true
        field.isBezeled = true
        field.bezelStyle = .roundedBezel
        field.alignment = .left
        field.baseWritingDirection = .leftToRight
        field.userInterfaceLayoutDirection = .leftToRight
        field.placeholderString = placeholder
        field.font = .systemFont(ofSize: NSFont.systemFontSize)
        field.setAccessibilityLabel(accessibilityTitle)
        field.setAccessibilityIdentifier("field." + accessibilityTitle)
        field.delegate = context.coordinator
        if multiline {
            field.usesSingleLineMode = false
            field.cell?.wraps = true
            field.cell?.isScrollable = false
            field.lineBreakMode = .byWordWrapping
            field.maximumNumberOfLines = 0
        } else {
            field.usesSingleLineMode = true
            field.cell?.wraps = false
            field.cell?.isScrollable = true
            field.lineBreakMode = .byClipping
        }
        field.setContentHuggingPriority(.defaultLow, for: .horizontal)
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }

    func updateNSView(_ field: NSTextField, context: Context) {
        context.coordinator.parent = self
        field.isEnabled = isEnabled
        field.placeholderString = placeholder
        guard field.stringValue != text else { return }
        // The binding may rewrite what was typed (for example, lowercasing an agent name). Keep the
        // insertion point where the user is typing instead of letting it jump.
        if let editor = field.currentEditor() {
            let old = field.stringValue as NSString, new = text as NSString
            let caret = editor.selectedRange.location + editor.selectedRange.length
            let position = max(0, min(new.length, caret - (old.length - new.length)))
            field.stringValue = text
            editor.selectedRange = NSRange(location: position, length: 0)
        } else {
            field.stringValue = text
        }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSTextField, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 300, height: multiline ? 64 : 24)
    }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: LeftAlignedTextField
        init(_ parent: LeftAlignedTextField) { self.parent = parent }
        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            parent.text = field.stringValue
        }
        func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
            // Return inserts a line in multi-line fields; Tab always moves to the next field.
            if parent.multiline, selector == #selector(NSResponder.insertNewline(_:)) { textView.insertNewlineIgnoringFieldEditor(nil); return true }
            return false
        }
    }
}

/// A multi-line, left-to-right plain-text area. Used for short prose such as an agent's purpose and
/// for YAML, so smart quotes and dashes are always off (curly quotes would break YAML).
struct LeftAlignedTextView: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String = ""
    var accessibilityTitle: String
    var font: NSFont = .systemFont(ofSize: NSFont.systemFontSize)
    var tinted = true
    @Environment(\.isEnabled) private var isEnabled

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        scroll.borderType = .noBorder
        let view = scroll.documentView as! NSTextView
        view.delegate = context.coordinator
        view.string = text
        view.font = font
        view.alignment = .left
        view.baseWritingDirection = .leftToRight
        view.userInterfaceLayoutDirection = .leftToRight
        view.isRichText = false
        view.allowsUndo = true
        view.isAutomaticQuoteSubstitutionEnabled = false
        view.isAutomaticDashSubstitutionEnabled = false
        view.isAutomaticTextReplacementEnabled = false
        view.isAutomaticSpellingCorrectionEnabled = false
        view.isContinuousSpellCheckingEnabled = false
        view.drawsBackground = tinted
        view.backgroundColor = NSColor.textBackgroundColor.withAlphaComponent(0.35)
        view.textContainerInset = NSSize(width: 5, height: 6)
        view.setAccessibilityLabel(accessibilityTitle)
        view.setAccessibilityIdentifier("field." + accessibilityTitle)
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let view = scroll.documentView as? NSTextView else { return }
        view.isEditable = isEnabled
        view.textColor = isEnabled ? .labelColor : .secondaryLabelColor
        if view.string != text {
            let selection = view.selectedRanges
            view.string = text
            view.selectedRanges = selection.filter { $0.rangeValue.upperBound <= (text as NSString).length }
        }
        if view.responds(to: NSSelectorFromString("setPlaceholderAttributedString:")) { view.setValue(NSAttributedString(string: placeholder, attributes: [.foregroundColor: NSColor.placeholderTextColor, .font: font]), forKey: "placeholderAttributedString") }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: LeftAlignedTextView
        init(_ parent: LeftAlignedTextView) { self.parent = parent }
        func textDidChange(_ notification: Notification) {
            guard let view = notification.object as? NSTextView else { return }
            parent.text = view.string
        }
        func textView(_ textView: NSTextView, doCommandBy selector: Selector) -> Bool {
            // Tab moves to the next field rather than inserting a tab character.
            if selector == #selector(NSResponder.insertTab(_:)) { textView.window?.selectNextKeyView(nil); return true }
            if selector == #selector(NSResponder.insertBacktab(_:)) { textView.window?.selectPreviousKeyView(nil); return true }
            return false
        }
    }
}

struct PendingSkill: Identifiable { let id = UUID(); let source: String; let name: String }

struct LaunchSheet: View {
    @Environment(\.openWindow) private var openWindow
    @EnvironmentObject private var app: AppModel
    @Environment(\.dismiss) private var dismiss
    let agent: MantleAgent
    @State private var directory = ""
    @State private var prompt = ""
    @State private var context = true
    @State private var root = ""
    @State private var session = ""
    @State private var advanced = false
    var body: some View {
        VStack(alignment:.leading,spacing:22) {
            PageHeader(title:"Start with \(agent.title)",subtitle:"Choose the project this agent will work in.",symbol:"play.circle")
            Surface(title:"Project folder") { HStack { Image(systemName:"folder").foregroundStyle(Color.mantleAccent); Text(directory.isEmpty ? "Choose a folder…" : directory).font(.callout).lineLimit(2).textSelection(.enabled); Spacer(); Button("Choose…") { pickFolder() } }; Toggle("Include this project’s AGENTS.md and CLAUDE.md",isOn:$context).font(.caption) }
            VStack(alignment:.leading,spacing:8) { Text("First message").font(.headline); TextField("Optional — you can also start in the session",text:$prompt,axis:.vertical).lineLimit(3...5).textFieldStyle(.roundedBorder) }
            DisclosureGroup("More options",isExpanded:$advanced) { VStack(spacing:12) { TextField("Project context root (optional)",text:$root); TextField("Resume session ID or path (optional)",text:$session) }.textFieldStyle(.roundedBorder).padding(.top,12) }
            HStack { Spacer(); Button("Use Terminal Instead…") { launch() }.font(.caption).disabled(directory.isEmpty) }
            Text("Your conversation opens in its own Mantle window. Instructions, tools, and session history stay with this agent.").font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction); Button("Start Session") { startNative() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(directory.isEmpty) }
        }.padding(28).frame(width:610)
        .task { if directory.isEmpty { let suggested = await app.suggestedDirectory(for: agent); if directory.isEmpty { directory = suggested } } }
    }
    private func pickFolder() { let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false; panel.prompt = "Use This Folder"; if panel.runModal() == .OK { directory = panel.url?.path ?? "" } }
    private func startNative() { let launch = SessionLaunch(agentName:agent.name,directory:directory,includeContext:context,projectRoot:root,sessionID:session); app.prepareSession(launch,firstMessage:prompt); openWindow(id:"live-session",value:launch); dismiss() }
    private func launch() { var args = ["agent","run",agent.name]; if !context { args.append("--no-project-context") }; if !root.isEmpty { args += ["--project-root",root] }; if !session.isEmpty { args += ["--session",session] }; if !prompt.isEmpty { args += ["--",prompt] }; app.rememberDirectory(directory,for:agent.name); app.terminal(args,directory:directory); dismiss() }
}

struct SessionsView: View {
    @Environment(\.openWindow) private var openWindow
    @EnvironmentObject private var app: AppModel
    let agent: MantleAgent?
    @State private var sessions:[JSON] = []
    @State private var selected:String?
    @State private var detail:JSON = .null
    @State private var loading = true
    @State private var search = ""
    @State private var failure:String?
    var body: some View {
        VStack(spacing:0) {
            // A fixed height because the window's minimum size measures this wrapping text at its narrowest,
            // which would otherwise push the whole layout past the bottom of the window.
            if agent == nil { PageHeader(title:"Pick up where you left off.",subtitle:"Your conversations, grouped around the work that matters.",symbol:"clock").padding(28).frame(height:108); Divider() }
            if loading { ProgressView("Loading sessions…").frame(maxWidth:.infinity,maxHeight:.infinity) }
            else if let failure { EmptyState(symbol:"exclamationmark.bubble",title:"Couldn’t load sessions",detail:failure); Button("Try Again") { Task { await load() } }.padding() }
            else if sessions.isEmpty { EmptyState(symbol:"bubble.left.and.bubble.right",title:"Your next conversation starts here.",detail:"Run an agent in a project to start a session. Its history stays on your Mac and appears here.") }
            else {
                // The list needs an explicit minimum, or its full content height overflows the window.
                HSplitView {
                    VStack(spacing:0) { TextField("Search sessions",text:$search).textFieldStyle(.roundedBorder).padding(14); List(sessions.filter { search.isEmpty || $0["title"].string.localizedCaseInsensitiveContains(search) },id: \.self,selection:$selected) { item in VStack(alignment:.leading,spacing:7) { Text(item["title"].string).font(.callout.weight(.medium)).lineLimit(2); Text(item["agent"].string + (item["retained"].bool ? " · deleted" : "")).font(.caption).foregroundStyle(Color.mantleAccent); Text(String(item["updatedAt"].string.prefix(10))).font(.caption2).foregroundStyle(.secondary) }.padding(.vertical,7).tag(item["path"].string) }.listStyle(.sidebar) }.frame(minWidth:220,idealWidth:280,maxWidth:330,minHeight:0,maxHeight:.infinity)
                    VStack(spacing:0) {
                        if let current = sessions.first(where:{$0["path"].string == selected}) {
                            HStack { VStack(alignment:.leading,spacing:5) { Text(current["title"].string).font(.headline).lineLimit(2); Text(current["cwd"].string).font(.caption).foregroundStyle(.secondary).lineLimit(1) }; Spacer(); if current["retained"].bool { StatusPill(label:"Agent deleted · read only",color:.secondary) } else { Button("Resume Session") { let folder = current["cwd"].string; if folder.isEmpty { app.error = "This session has no saved project folder. Start the agent and choose a project, then enter this session path in More Options." } else { let launch = SessionLaunch(agentName:current["agent"].string,directory:folder,sessionID:current["path"].string); app.prepareSession(launch); openWindow(id:"live-session",value:launch) } } } }.padding(20)
                            Divider()
                            ScrollView { LazyVStack(alignment:.leading,spacing:18) { ForEach(Array(detail["messages"].array.enumerated()),id: \.offset) { _,message in VStack(alignment:.leading,spacing:8) { Label(message["role"].string.capitalized,systemImage:message["role"].string == "user" ? "person.crop.circle" : "sparkles").font(.caption.weight(.semibold)).foregroundStyle(Color.mantleAccent); Text(message["text"].string).font(.system(size:13)).lineSpacing(4).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading) }.padding(18).background(.primary.opacity(0.035),in:RoundedRectangle(cornerRadius:12)) }; if detail["truncated"].bool { Text("Showing the latest 1,000 messages. Resume in Terminal for the full session.").font(.caption).foregroundStyle(.secondary) } }.padding(22) }
                        } else { EmptyState(symbol:"text.bubble",title:"Choose a session",detail:"Read the conversation or continue from where you left off.") }
                    }.frame(minWidth:350,maxWidth:.infinity,minHeight:0,maxHeight:.infinity)
                }.frame(minHeight:0,maxHeight:.infinity)
            }
        }.task { await load() }.onChange(of:selected) { _,value in Task { await select(value) } }
    }
    private func load() async { loading = true; defer { loading = false }; do { sessions = try await app.request("sessions.list",agent.map { ["agent":.string($0.name)] } ?? [:]).array; failure = nil } catch { failure = error.localizedDescription } }
    private func select(_ path:String?) async { guard let path, let item = sessions.first(where:{$0["path"].string == path}) else { detail = .null; return }; do { var parameters:[String:JSON] = ["agent":item["agent"],"path":.string(path)]; if item["retained"].bool { parameters["retainedFolder"] = item["retainedFolder"] }; let result = try await app.request("sessions.read",parameters); if selected == path { detail = result } } catch { app.error = error.localizedDescription } }
}
