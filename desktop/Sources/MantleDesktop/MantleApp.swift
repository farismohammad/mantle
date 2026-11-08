import SwiftUI
import AppKit

@main
struct MantleDesktopApp: App {
    @StateObject private var app = AppModel()
    @AppStorage("appearance") private var appearance = "dark"
    var body: some Scene {
        WindowGroup("Mantle") {
            RootView().environmentObject(app)
                .tint(.mantleAccent)
                .preferredColorScheme(appearance == "system" ? nil : appearance == "light" ? .light : .dark)
                .frame(minWidth: 1020,minHeight: 680)
                .task { await app.refresh() }
        }
        .defaultSize(width: 1280,height: 820)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) { Button("New Agent…") { app.showCreate = true }.keyboardShortcut("n") }
            CommandMenu("Agent") {
                Button("Run Agent…") { app.launchAgent = app.selectedAgent }.keyboardShortcut("r").disabled(app.selectedAgent == nil)
                Button("Edit Configuration…") { app.editingAgent = app.selectedAgent }.keyboardShortcut("e").disabled(app.selectedAgent == nil)
                Divider()
                Button("Refresh") { Task { await app.refresh() } }.keyboardShortcut("r",modifiers: [.command,.shift])
            }
        }
        WindowGroup("Mantle Session", id: "live-session", for: SessionLaunch.self) { $launch in
            if let launch, !app.openedLaunches.contains(launch.launchID) {
                ClosedSessionView().environmentObject(app).tint(.mantleAccent)
                    .preferredColorScheme(appearance == "system" ? nil : appearance == "light" ? .light : .dark)
            } else if let launch, let agent = app.agents.first(where: { $0.name == launch.agentName }) {
                LiveSessionView(agent: agent, directory: launch.directory, launchID: launch.launchID, includeContext: launch.includeContext, projectRoot: launch.projectRoot, sessionID: launch.sessionID)
                    .environmentObject(app).tint(.mantleAccent)
                    .preferredColorScheme(appearance == "system" ? nil : appearance == "light" ? .light : .dark)
            } else { EmptyState(symbol: "square.stack.3d.up", title: "Agent unavailable", detail: "Open your agent library, then start or resume a session.") }
        }.defaultSize(width: 1000, height: 760)
        // The agent editor is its own window so it can be moved, resized, and kept beside the library.
        // The value is the agent's name, or an empty string for a new agent.
        WindowGroup("Agent", id: "agent-editor", for: String.self) { $name in
            AgentEditorWindow(name: name ?? "").environmentObject(app).tint(.mantleAccent)
                .preferredColorScheme(appearance == "system" ? nil : appearance == "light" ? .light : .dark)
        }
        .defaultSize(width: 760, height: 820)
        .windowResizability(.contentMinSize)
        Settings {
            Form {
                Picker("Appearance",selection: $appearance) { Text("Dark").tag("dark"); Text("Light").tag("light"); Text("System").tag("system") }
                LabeledContent("Agent storage") { Text(app.home).textSelection(.enabled).font(.caption) }
                Text("Mantle uses the same agents, logins, and runtimes as the command-line app.").font(.caption).foregroundStyle(.secondary)
                Button("Show in Finder") { NSWorkspace.shared.selectFile(nil,inFileViewerRootedAtPath: app.home) }
            }.formStyle(.grouped).padding().frame(width: 500,height: 230).tint(.mantleAccent)
        }
    }
}

/// Hosts the agent editor in a standalone window. Closing the window discards unsaved edits.
struct AgentEditorWindow: View {
    @EnvironmentObject private var app: AppModel
    @Environment(\.dismiss) private var dismiss
    let name: String
    var body: some View {
        Group {
            if name.isEmpty { AgentEditor(agent: nil) }
            else if let agent = app.agents.first(where: { $0.name == name }) { AgentEditor(agent: agent) }
            else if app.loading { ProgressView().frame(width: 400, height: 300) }
            else { EmptyState(symbol: "square.stack.3d.up", title: "Agent unavailable", detail: "This agent no longer exists.").frame(width: 480, height: 320) }
        }
        .navigationTitle(name.isEmpty ? "New Agent" : "Edit \(name)")
    }
}

struct ClosedSessionView: View {
    @EnvironmentObject private var app: AppModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(spacing: 0) {
            EmptyState(symbol: "bubble.left.and.bubble.right", title: "This session has ended", detail: "It closed when Mantle quit, and nothing was sent when this window reopened. Its history is saved in Sessions.")
            Button("Open Sessions") { app.destination = .sessions; app.showMainWindow(); dismiss() }.buttonStyle(.borderedProminent).padding(.bottom, 36)
        }.frame(minWidth: 480, minHeight: 320)
    }
}

struct RootView: View {
    @Environment(\.openWindow) private var openWindow
    @Environment(\.colorScheme) private var colorScheme
    @EnvironmentObject private var app: AppModel
    @State private var columnVisibility = NavigationSplitViewVisibility.all
    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            VStack(spacing: 0) {
                HStack(spacing: 11) { MantleMark(size: 34); VStack(alignment: .leading,spacing: 2) { Text("Mantle").font(.system(size: 18,weight: .semibold,design: .rounded)); Text("A little more focused.").font(.caption2).foregroundStyle(.secondary) }; Spacer() }.padding(.horizontal,20).padding(.vertical,23)
                List(selection: $app.destination) {
                    Section("Workspace") { sidebarRow(.agents); sidebarRow(.sessions) }
                    Section("Library") { sidebarRow(.registry) }
                    Section("Manage") { sidebarRow(.connections); sidebarRow(.runtimes); sidebarRow(.health) }
                }.listStyle(.sidebar)
                VStack(alignment: .leading,spacing: 10) {
                    Divider()
                    Label("On this Mac",systemImage: "desktopcomputer").font(.caption.weight(.medium))
                    Text("\(app.agents.count) agent\(app.agents.count == 1 ? "" : "s") · Local first").font(.caption2).foregroundStyle(.secondary)
                    HStack { SettingsLink { Label("Settings",systemImage: "gearshape") }.buttonStyle(.plain).font(.caption); Spacer(); Button { Task { await app.refresh() } } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.plain).help("Refresh Mantle") }
                }.padding(20)
            }.navigationSplitViewColumnWidth(min: 200,ideal: 220,max: 260)
        } detail: {
            VStack(spacing: 0) {
                if !app.snapshot["warnings"].array.isEmpty {
                    HStack(alignment: .top,spacing: 9) { Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange); Text(app.snapshot["warnings"].array.map(\.string).joined(separator: "\n")).font(.caption).lineLimit(4).textSelection(.enabled); Spacer() }.padding(.horizontal,20).padding(.vertical,11).background(Color.orange.opacity(0.08))
                }
                if app.loading { ProgressView("Opening your workspace…").frame(maxWidth: .infinity,maxHeight: .infinity) }
                else { mainContent }
                if let notice = app.notice {
                    HStack(spacing: 9) { Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.mantleAccent); Text(notice).font(.caption); Spacer(); Button { app.notice = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).help("Dismiss notification") }.padding(.horizontal,20).padding(.vertical,11).background(.bar)
                }
            }
            .background(colorScheme == .dark ? Color.mantleCanvas : Color(nsColor: .windowBackgroundColor))
            .navigationTitle(app.destination.rawValue)
            .toolbar {
                if app.busy { ToolbarItem { ProgressView().controlSize(.small) } }
                if app.destination == .agents {
                    ToolbarItemGroup {
                        Button { app.showCreate = true } label: { Label("New Agent",systemImage: "plus") }.help("New Agent (⌘N)")
                        if let selected = app.selectedAgent {
                            Button { app.editingAgent = selected } label: { Label("Edit",systemImage: "slider.horizontal.3") }.help("Edit Configuration (⌘E)")
                            Button { app.launchAgent = selected } label: { Label("Run Agent",systemImage: "play.fill") }.buttonStyle(.borderedProminent).labelStyle(.titleAndIcon).help("Run Agent (⌘R)")
                        }
                    }
                }
            }
        }
        // New and edited agents open in their own movable window rather than a sheet attached to this one.
        .onChange(of: app.showCreate) { _,show in if show { app.showCreate = false; openWindow(id: "agent-editor", value: "") } }
        .onChange(of: app.editingAgent) { _,agent in if let agent { app.editingAgent = nil; openWindow(id: "agent-editor", value: agent.name) } }
        .sheet(item: $app.launchAgent) { agent in LaunchSheet(agent: agent).environmentObject(app) }
        .alert("Couldn’t complete the action",isPresented: Binding(get: { app.error != nil },set: { if !$0 { app.error = nil } })) { Button("OK") { app.error = nil } } message: { Text(app.error ?? "") }
    }
    @ViewBuilder private var mainContent: some View {
        switch app.destination {
        case .agents: AgentWorkspace()
        case .sessions: SessionsView(agent: nil)
        case .registry: RegistryView()
        case .connections: ConnectionsView()
        case .runtimes: RuntimesView()
        case .health: HealthView()
        }
    }
    private func sidebarRow(_ item: Destination) -> some View {
        HStack { Label(item.rawValue,systemImage: item.symbol); Spacer(); if item == .agents && !app.agents.isEmpty { Text("\(app.agents.count)").font(.caption).foregroundStyle(.secondary) } }.tag(item).padding(.vertical,5)
    }
}

struct AgentWorkspace: View {
    @EnvironmentObject private var app: AppModel
    @Environment(\.openWindow) private var openWindow
    private func agent(for ids: Set<String>) -> MantleAgent? { ids.count == 1 ? app.agents.first { ids.contains($0.id) } : nil }
    /// Double-click opens a chat in the agent's last folder; with no known folder it asks for one.
    private func quickStart(_ agent: MantleAgent) {
        Task {
            let directory = await app.suggestedDirectory(for: agent)
            if directory.isEmpty { app.launchAgent = agent; return }
            let launch = SessionLaunch(agentName: agent.name, directory: directory)
            app.prepareSession(launch); openWindow(id: "live-session", value: launch)
        }
    }
    var body: some View {
        if app.agents.isEmpty { WelcomeView() }
        else {
            HSplitView {
                VStack(spacing: 0) {
                    HStack { Image(systemName: "magnifyingglass").foregroundStyle(.tertiary); TextField("Find an agent",text: $app.search).textFieldStyle(.plain).accessibilityLabel("Search agents"); if !app.search.isEmpty { Button { app.search = "" } label: { Image(systemName: "xmark.circle.fill") }.buttonStyle(.plain) } }.padding(10).background(.primary.opacity(0.035),in: RoundedRectangle(cornerRadius: 9)).padding(16)
                    HStack { Text("YOUR AGENTS").font(.system(size: 10,weight: .semibold)).tracking(1.3); Spacer(); Text("\(app.filteredAgents.count)").font(.caption) }.foregroundStyle(.secondary).padding(.horizontal,21).padding(.bottom,8)
                    List(app.filteredAgents,selection: $app.selection) { agent in
                        HStack(alignment: .top,spacing: 11) { Image(systemName: agent.symbol).font(.system(size: 17)).foregroundStyle(Color.mantleAccent).frame(width: 34,height: 34).background(Color.mantleAccent.opacity(0.10),in: RoundedRectangle(cornerRadius: 9)); VStack(alignment: .leading,spacing: 6) { Text(agent.title).font(.system(size: 13,weight: .semibold)); Text(agent.description.isEmpty ? agent.model : agent.description).font(.caption).foregroundStyle(.secondary).lineLimit(2); Text(agent.provider).font(.system(size: 10,weight: .medium)).foregroundStyle(.tertiary) }; Spacer(minLength: 0) }.padding(.vertical,9).tag(agent.id)
                    }.listStyle(.sidebar)
                    .contextMenu(forSelectionType: String.self) { ids in
                        if let agent = agent(for: ids) {
                            Button("Start Chat") { quickStart(agent) }
                            Button("Run Agent…") { app.launchAgent = agent }
                            Button("Edit Configuration…") { app.editingAgent = agent }
                        }
                    } primaryAction: { ids in if let agent = agent(for: ids) { quickStart(agent) } }
                    if app.filteredAgents.isEmpty { Text("No agents match your search.").font(.caption).foregroundStyle(.secondary).padding() }
                    if !app.snapshot["damaged"].object.isEmpty {
                        VStack(alignment: .leading,spacing: 8) {
                            Label(app.snapshot["damaged"].object.count == 1 ? "1 agent couldn’t be loaded" : "\(app.snapshot["damaged"].object.count) agents couldn’t be loaded",systemImage: "exclamationmark.triangle.fill").font(.caption.weight(.semibold)).foregroundStyle(.orange)
                            ForEach(app.snapshot["damaged"].object.keys.sorted(),id: \.self) { name in Text("\(name): \(app.snapshot["damaged"][name].string)").font(.caption2).foregroundStyle(.secondary).lineLimit(3).textSelection(.enabled) }
                            Button("Open Health") { app.destination = .health }.controlSize(.small)
                        }.padding(14).frame(maxWidth: .infinity,alignment: .leading)
                    }
                }.frame(minWidth: 230,idealWidth: 270,maxWidth: 340)
                if let agent = app.selectedAgent { AgentDetail(agent: agent).id(agent.id).frame(minWidth: 500,maxWidth: .infinity,maxHeight: .infinity) }
                else { EmptyState(symbol: "square.stack.3d.up",title: "Choose an agent",detail: "Each agent has its own purpose, skills, and workspace.") }
            }
        }
    }
}

struct WelcomeView: View {
    @EnvironmentObject private var app: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading,spacing: 28) {
                HStack { Spacer(); MantleMark(size: 80); Spacer() }.padding(.top,36)
                VStack(spacing: 10) { Text("Make room for focused work.").font(.system(size: 32,weight: .bold,design: .rounded)); Text("Give each agent a purpose. Mantle keeps its instructions,\nskills, and sessions together — right here on your Mac.").font(.system(size: 14)).foregroundStyle(.secondary).multilineTextAlignment(.center).lineSpacing(5) }.frame(maxWidth: .infinity)
                HStack(spacing: 12) { Spacer(); Button("Create Your First Agent") { app.showCreate = true }.buttonStyle(.borderedProminent).controlSize(.large); Button("Browse a Registry") { app.destination = .registry }.controlSize(.large); Spacer() }
                HStack(alignment: .top,spacing: 16) {
                    welcomeCard("01","Give it a purpose","Choose a model and instructions for a specific kind of work.","square.stack.3d.up")
                    welcomeCard("02","Make it capable","Add just the skills, packages, and connections it needs.","puzzlepiece.extension")
                    welcomeCard("03","Start in a project","Run your agent with the right context and pick up any session.","play.circle")
                }.padding(.top,18)
                HStack { Image(systemName: "externaldrive").foregroundStyle(Color.mantleAccent); Text("Already use Mantle? Your existing ~/.mantle library appears automatically.").font(.caption).foregroundStyle(.secondary) }.frame(maxWidth: .infinity).padding(.top,4)
                if !app.snapshot["damaged"].object.isEmpty { Surface(title: "Some agents need attention") { ForEach(app.snapshot["damaged"].object.keys.sorted(),id: \.self) { name in Text("\(name): \(app.snapshot["damaged"][name].string)").font(.caption).foregroundStyle(.orange) }; Button("Open Health") { app.destination = .health } } }
            }.padding(40).frame(maxWidth: 960).frame(maxWidth: .infinity)
        }
    }
    private func welcomeCard(_ number: String,_ title: String,_ detail: String,_ symbol: String) -> some View {
        Surface { HStack { Image(systemName: symbol).font(.title2).foregroundStyle(Color.mantleAccent); Spacer(); Text(number).font(.caption.monospaced()).foregroundStyle(.tertiary) }; Text(title).font(.headline); Text(detail).font(.callout).foregroundStyle(.secondary).lineSpacing(4).frame(maxWidth: .infinity,alignment: .leading) }.frame(maxWidth: .infinity)
    }
}
