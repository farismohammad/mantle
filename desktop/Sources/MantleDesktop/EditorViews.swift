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
    var body: some View {
        VStack(spacing:0) {
            HStack(alignment:.top) { PageHeader(title:agent == nil ? "Create an Agent" : "Edit \(agent!.title)",subtitle:"Give it a clear purpose and just what it needs.",symbol:"square.stack.3d.up"); Picker("Editor mode",selection:$mode) { Text("Details").tag("Details"); Text("YAML").tag("YAML") }.pickerStyle(.segmented).labelsHidden().frame(width:160).disabled(saving || switching) }.padding(28)
            Divider()
            if mode == "Details" {
                Form {
                    Section("Identity") {
                        TextField("Name",text:$name,prompt:Text("e.g. code-reviewer")).disabled(agent != nil).autocorrectionDisabled()
                        Text("Lowercase letters, numbers, hyphens, and underscores.").font(.caption).foregroundStyle(.secondary)
                        TextField("Purpose",text:$purpose,prompt:Text("What is this agent here to do?"),axis:.vertical).lineLimit(2...3)
                    }
                    Section("Model") {
                        Picker("Provider",selection:Binding(get:{provider},set:{ value in provider = value; if let selected = app.providers.first(where:{$0["id"].string == value}), !selected["default_model"].string.isEmpty { model = selected["default_model"].string } })) { ForEach(app.providers,id: \.self) { p in Text(p["name"].string).tag(p["id"].string) }; if !app.providers.contains(where:{$0["id"].string == provider}) { Text(provider).tag(provider) } }
                        TextField("Model",text:$model)
                        TextField("Pi version",text:$runtime,prompt:Text("latest"))
                    }
                    Section("Instructions") {
                        Text("Describe how this agent should work, what it should pay attention to, and what a good result looks like.").font(.caption).foregroundStyle(.secondary)
                        TextEditor(text:$instructions).font(.system(size:13)).frame(minHeight:130).scrollContentBackground(.hidden).padding(8).background(.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:8)).accessibilityLabel("System instructions")
                    }
                    Section("Available tools") {
                        Picker("Files",selection:$filesystem) { Text("Read and write").tag("all"); Text("Read only").tag("read"); Text("No file tools").tag("none") }
                        Toggle("Shell access",isOn:Binding(get:{shell == "all"},set:{shell = $0 ? "all" : "none"}))
                        Text("Tool choices are not an operating-system sandbox. Configure MCP servers, secret names, and resource declarations in YAML.").font(.caption).foregroundStyle(.secondary)
                    }
                }.formStyle(.grouped)
            } else {
                VStack(alignment:.leading,spacing:12) {
                    HStack { Text("The complete Mantle configuration, including MCP and resources.").font(.caption).foregroundStyle(.secondary); Spacer(); Button("Format") { Task { await format() } }.disabled(saving) }
                    TextEditor(text:$yaml).font(.system(size:12,design:.monospaced)).scrollContentBackground(.hidden).padding(12).background(.primary.opacity(0.035),in:RoundedRectangle(cornerRadius:10)).accessibilityLabel("Agent YAML configuration")
                    Text("Resource declarations describe installs. Use the Skills and Packages tabs to install or remove them.").font(.caption).foregroundStyle(.secondary)
                }.padding(24)
            }
            Divider()
            VStack(alignment:.leading,spacing:12) {
                if let validation { ScrollView { Text(validation).font(.caption).foregroundStyle(valid ? .green : .red).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading) }.frame(maxHeight:80) }
                HStack { if saving || switching { ProgressView().controlSize(.small) }; Button("Validate") { Task { await validate() } }.disabled(saving || switching); Spacer(); Button("Cancel") { discard = true }.keyboardShortcut(.cancelAction).disabled(saving); Button(agent == nil ? "Create Agent" : "Save Changes") { Task { await save() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(saving || switching || (mode == "Details" && name.isEmpty)) }
            }.padding(22)
        }.frame(width:750,height:780).interactiveDismissDisabled().task { initialize() }
        .onChange(of:mode) { old,new in guard initialized, !switching, old != new else { return }; Task { await switchMode(from:old,to:new) } }
        .alert("Discard these changes?",isPresented:$discard) { Button("Keep Editing",role:.cancel) {}; Button("Discard Changes",role:.destructive) { dismiss() } } message: { Text("Your saved agent will remain as it is.") }
    }
    private func initialize() { guard !initialized else { return }; if let agent { base = agent.config.object; yaml = agent.yaml; populate(agent.config) } else { if let signed = app.credentials.first, let p = app.providers.first(where:{$0["id"].string == signed["provider"].string}) { provider = p["id"].string; if !p["default_model"].string.isEmpty { model = p["default_model"].string } }; base = ["mantle":.string("0.1"),"resources":.object(["skills":.array([]),"packages":.array([])]),"mcp":.object(["servers":.object([:])]),"secrets":.array([])] }; initialized = true }
    private func populate(_ config: JSON) { name = config["agent"]["name"].string; purpose = config["agent"]["description"].string; provider = config["model"]["provider"].string; model = config["model"]["name"].string; runtime = config["runtime"]["version"].string; instructions = config["system_prompt"].string; filesystem = config["permissions"]["filesystem"].string; shell = config["permissions"]["shell"].string }
    private var draft: String { var value = base; value["agent"] = .object(["name":.string(name),"description":.string(purpose)]); value["model"] = .object(["provider":.string(provider),"name":.string(model)]); value["runtime"] = .object(["name":.string("pi"),"version":.string(runtime)]); value["system_prompt"] = instructions.isEmpty ? .null : .string(instructions); value["permissions"] = .object(["filesystem":.string(filesystem),"shell":.string(shell)]); return JSON.object(value).pretty }
    private var text: String { mode == "YAML" ? yaml : draft }
    private func parsed(_ text: String) async throws -> JSON { var params:[String:JSON] = ["yaml":.string(text)]; if let agent { params["name"] = .string(agent.name) }; return try await app.request("agent.format",params) }
    private func switchMode(from old:String,to new:String) async { switching = true; defer { switching = false }; do { let value = try await parsed(old == "Details" ? draft : yaml); if new == "YAML" { yaml = value["yaml"].string } else { base = value["config"].object; populate(value["config"]) }; validation = nil } catch { if new == "YAML" { yaml = draft; validation = "Complete the configuration before saving."; valid = false } else { mode = old; validation = error.localizedDescription; valid = false } } }
    private func format() async { do { yaml = try await parsed(yaml)["yaml"].string; validation = "Configuration formatted."; valid = true } catch { validation = error.localizedDescription; valid = false } }
    private func validate() async { do { _ = try await parsed(text); validation = "Configuration is valid."; valid = true } catch { validation = error.localizedDescription; valid = false } }
    private func save() async { saving = true; defer { saving = false }; do { let value = try await parsed(text); var params:[String:JSON] = ["name":value["config"]["agent"]["name"],"yaml":.string(mode == "YAML" ? yaml : value["yaml"].string),"confirmed":.bool(true),"preserveComments":.bool(mode == "Details")]; if let agent { params["expectedDigest"] = agent.raw["digest"] }; let result = try await app.request(agent == nil ? "agent.create" : "agent.save",params); await app.refresh(); app.selection = result["name"].string; app.destination = .agents; app.notice = agent == nil ? "Agent created. Add skills or start a session when you’re ready." : "Changes saved. A copy of the previous configuration is in Backups."; dismiss() } catch { validation = error.localizedDescription; valid = false } }
}

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
    }
    private func pickFolder() { let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false; panel.prompt = "Use This Folder"; if panel.runModal() == .OK { directory = panel.url?.path ?? "" } }
    private func startNative() { openWindow(id:"live-session",value:SessionLaunch(agentName:agent.name,directory:directory,prompt:prompt,includeContext:context,projectRoot:root,sessionID:session)); dismiss() }
    private func launch() { var args = ["agent","run",agent.name]; if !context { args.append("--no-project-context") }; if !root.isEmpty { args += ["--project-root",root] }; if !session.isEmpty { args += ["--session",session] }; if !prompt.isEmpty { args += ["--",prompt] }; app.terminal(args,directory:directory); dismiss() }
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
            if agent == nil { PageHeader(title:"Pick up where you left off.",subtitle:"Your conversations, grouped around the work that matters.",symbol:"clock").padding(28); Divider() }
            if loading { ProgressView("Loading sessions…").frame(maxWidth:.infinity,maxHeight:.infinity) }
            else if let failure { EmptyState(symbol:"exclamationmark.bubble",title:"Couldn’t load sessions",detail:failure); Button("Try Again") { Task { await load() } }.padding() }
            else if sessions.isEmpty { EmptyState(symbol:"bubble.left.and.bubble.right",title:"Your next conversation starts here.",detail:"Run an agent in a project to start a session. Its history stays on your Mac and appears here.") }
            else {
                HSplitView {
                    VStack(spacing:0) { TextField("Search sessions",text:$search).textFieldStyle(.roundedBorder).padding(14); List(sessions.filter { search.isEmpty || $0["title"].string.localizedCaseInsensitiveContains(search) },id: \.self,selection:$selected) { item in VStack(alignment:.leading,spacing:7) { Text(item["title"].string).font(.callout.weight(.medium)).lineLimit(2); Text(item["agent"].string).font(.caption).foregroundStyle(Color.mantleAccent); Text(String(item["updatedAt"].string.prefix(10))).font(.caption2).foregroundStyle(.secondary) }.padding(.vertical,7).tag(item["path"].string) }.listStyle(.sidebar) }.frame(minWidth:220,idealWidth:280,maxWidth:330)
                    VStack(spacing:0) {
                        if let current = sessions.first(where:{$0["path"].string == selected}) {
                            HStack { VStack(alignment:.leading,spacing:5) { Text(current["title"].string).font(.headline).lineLimit(2); Text(current["cwd"].string).font(.caption).foregroundStyle(.secondary).lineLimit(1) }; Spacer(); Button("Resume Session") { let folder = current["cwd"].string; if folder.isEmpty { app.error = "This session has no saved project folder. Start the agent and choose a project, then enter this session path in More Options." } else { openWindow(id:"live-session",value:SessionLaunch(agentName:current["agent"].string,directory:folder,sessionID:current["path"].string)) } } }.padding(20)
                            Divider()
                            ScrollView { LazyVStack(alignment:.leading,spacing:18) { ForEach(Array(detail["messages"].array.enumerated()),id: \.offset) { _,message in VStack(alignment:.leading,spacing:8) { Label(message["role"].string.capitalized,systemImage:message["role"].string == "user" ? "person.crop.circle" : "sparkles").font(.caption.weight(.semibold)).foregroundStyle(Color.mantleAccent); Text(message["text"].string).font(.system(size:13)).lineSpacing(4).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading) }.padding(18).background(.primary.opacity(0.035),in:RoundedRectangle(cornerRadius:12)) }; if detail["truncated"].bool { Text("Showing the latest 1,000 messages. Resume in Terminal for the full session.").font(.caption).foregroundStyle(.secondary) } }.padding(22) }
                        } else { EmptyState(symbol:"text.bubble",title:"Choose a session",detail:"Read the conversation or continue from where you left off.") }
                    }.frame(minWidth:350,maxWidth:.infinity,maxHeight:.infinity)
                }
            }
        }.task { await load() }.onChange(of:selected) { _,value in Task { await select(value) } }
    }
    private func load() async { loading = true; defer { loading = false }; do { sessions = try await app.request("sessions.list",agent.map { ["agent":.string($0.name)] } ?? [:]).array; failure = nil } catch { failure = error.localizedDescription } }
    private func select(_ path:String?) async { guard let path, let item = sessions.first(where:{$0["path"].string == path}) else { detail = .null; return }; do { let result = try await app.request("sessions.read",["agent":item["agent"],"path":.string(path)]); if selected == path { detail = result } } catch { app.error = error.localizedDescription } }
}
