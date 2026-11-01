import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct AgentDetail: View {
    @EnvironmentObject private var app: AppModel
    let agent: MantleAgent
    @State private var tab = "Overview"
    @State private var resourceKind: String?
    @State private var showDelete = false
    @State private var showAuthChange = false
    @State private var cloudAction: String?
    @State private var difference: String?
    private let tabs = ["Overview", "Skills", "Packages", "Connections", "Sessions", "YAML"]
    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading,spacing: 23) {
                HStack(alignment: .top) {
                    PageHeader(title: agent.title,subtitle: agent.description.isEmpty ? "A focused workspace for your agent." : agent.description,symbol: agent.symbol)
                    Menu {
                        Button("Show in Finder") { NSWorkspace.shared.selectFile(nil,inFileViewerRootedAtPath: agent.path) }
                        Button("Export Configuration…") { exportConfiguration() }
                        Divider()
                        Button(agent.isolated ? "Use Shared Logins…" : "Use Separate Logins…") { showAuthChange = true }
                        Divider()
                        Button("Delete Agent…",role: .destructive) { showDelete = true }
                    } label: { Image(systemName: "ellipsis.circle").font(.title3) }.menuStyle(.borderlessButton).frame(width: 28).help("More agent actions")
                }
                HStack(spacing: 8) { StatusPill(label: agent.provider); StatusPill(label: agent.model,color: .secondary); Spacer(); Label("On this Mac",systemImage: "internaldrive").font(.caption).foregroundStyle(.secondary) }
                Picker("Agent section",selection: $tab) { ForEach(tabs,id: \.self) { Text($0).tag($0) } }.pickerStyle(.segmented).labelsHidden()
            }.padding(28)
            Divider()
            if tab == "Sessions" { SessionsView(agent: agent) }
            else {
                ScrollView {
                    VStack(alignment: .leading,spacing: 22) {
                        switch tab {
                        case "Skills": resources(agent.skills,kind: "skills")
                        case "Packages": resources(agent.packages,kind: "packages")
                        case "Connections": connections
                        case "YAML": Surface(title: "Agent configuration") { HStack { Text("The complete, portable definition of this agent.").font(.callout).foregroundStyle(.secondary); Spacer(); Button("Edit") { app.editingAgent = agent }; Button("Copy") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(agent.yaml,forType: .string); app.notice = "Configuration copied." } }; Text(agent.yaml).font(.system(size: 12,design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity,alignment: .leading) }
                        default: overview
                        }
                    }.padding(28).frame(maxWidth: 1000).frame(maxWidth: .infinity)
                }
            }
        }
        .sheet(isPresented: Binding(get: { resourceKind != nil },set: { if !$0 { resourceKind = nil } })) { AddResourceSheet(agent: agent,kind: resourceKind ?? "skills").environmentObject(app) }
        .alert("Delete \(agent.title)?",isPresented: $showDelete) { Button("Cancel",role: .cancel) {}; Button("Delete Agent",role: .destructive) { Task { await app.perform("agent.delete",["name":.string(agent.name),"confirmed":.bool(true),"keepSessions":.bool(true)],success:"Agent deleted. Its sessions stay readable in Sessions, and its configuration was backed up.") } } } message: { Text("The agent and installed resources will be removed. Its sessions stay readable in Sessions, and its configuration is backed up.") }
        .alert(agent.isolated ? "Use shared provider logins?" : "Give this agent separate logins?",isPresented: $showAuthChange) { Button("Cancel",role: .cancel) {}; Button("Confirm") { Task { await app.perform(agent.isolated ? "auth.share" : "auth.isolate",["agent":.string(agent.name),"confirmed":.bool(true),"copyCredentials":.bool(false)],success:"Login scope updated.") } } } message: { Text(agent.isolated ? "This agent will use the same provider logins as your other agents." : "This agent will need its own provider credentials. Shared logins will remain available to your other agents.") }
        .alert("\(cloudAction == "push" ? "Publish" : "Update") \(agent.title)?",isPresented: Binding(get:{ cloudAction != nil },set:{if !$0 {cloudAction = nil}})) { Button("Cancel",role:.cancel) { cloudAction = nil }; Button("Confirm") { let action = cloudAction ?? "push"; cloudAction = nil; Task { await app.perform("cloud." + action,["name":.string(agent.name),"confirmed":.bool(true)],success:action == "push" ? "Configuration published to your registry." : "Agent updated from your registry.") } } } message: { Text(cloudAction == "push" ? "Only agent.yaml is published to \(app.cloudURL). Review it for any credentials you may have placed in the configuration." : "The registry configuration will replace your local configuration. Mantle keeps a backup first.") }
        .sheet(isPresented: Binding(get:{difference != nil},set:{if !$0 {difference = nil}})) { VStack(alignment:.leading,spacing:16) { Text("Compare with Registry").font(.title2.bold()); ScrollView([.vertical,.horizontal]) { Text(difference ?? "").font(.system(size:12,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading) }; HStack { Spacer(); Button("Done") { difference = nil }.keyboardShortcut(.defaultAction) } }.padding(28).frame(width:760,height:540) }
    }
    private var overview: some View {
        VStack(alignment: .leading,spacing: 22) {
            HStack(spacing: 12) { metric("Skills",value: agent.skills.count,symbol: "sparkles",tab: "Skills"); metric("Packages",value: agent.packages.count,symbol: "shippingbox",tab: "Packages"); metric("Sessions",value: agent.sessionCount,symbol: "clock",tab: "Sessions") }
            if !agent.raw["resourceWarnings"].array.isEmpty { Surface(title: "Resources need attention") { ForEach(agent.raw["resourceWarnings"].array.map(\.string),id: \.self) { warning in Label(warning,systemImage: "exclamationmark.triangle.fill").font(.callout).foregroundStyle(.orange).textSelection(.enabled) } } }
            Surface(title: "Purpose & instructions") {
                let prompt = agent.config["system_prompt"].string
                Text(prompt.isEmpty ? "Add instructions to define how this agent should approach its work." : prompt).font(.system(size: 13)).lineSpacing(5).foregroundStyle(prompt.isEmpty ? .secondary : .primary).textSelection(.enabled)
                if prompt.isEmpty { Button("Add Instructions") { app.editingAgent = agent } }
            }
            Surface(title: "Environment") {
                Grid(alignment: .leading,horizontalSpacing: 35,verticalSpacing: 15) {
                    infoRow("Runtime","Pi · \(agent.runtime)")
                    infoRow("Model",agent.model)
                    infoRow("Provider logins",agent.isolated ? "Separate for this agent" : "Shared across your agents")
                    infoRow("File access",agent.config["permissions"]["filesystem"].string.capitalized)
                    infoRow("Shell access",agent.config["permissions"]["shell"].string == "all" ? "Enabled" : "Disabled")
                }
                Text("Permissions select the tools available to Pi. They do not confine the agent to an operating-system sandbox.").font(.caption).foregroundStyle(.secondary)
            }
            Surface(title: "Registry") {
                if app.cloudURL.isEmpty { HStack { VStack(alignment:.leading,spacing:5) { Text("Take this agent with you.").font(.callout.weight(.medium)); Text("Connect your registry to share configurations across your machines.").font(.caption).foregroundStyle(.secondary) }; Spacer(); Button("Connect…") { app.destination = .registry } } }
                else { Text(app.cloudURL).font(.callout).foregroundStyle(.secondary).textSelection(.enabled); HStack { Button("Publish…") { cloudAction = "push" }; Button("Compare") { Task { if let result = await app.perform("cloud.diff",["name":.string(agent.name)]) { difference = result["diff"].string.isEmpty ? "Your local configuration matches the registry." : result["diff"].string } } }; Button("Update from Registry…") { cloudAction = "update" } }.disabled(app.busy) }
            }
        }
    }
    private func metric(_ title: String,value: Int,symbol: String,tab target: String) -> some View {
        Button { tab = target } label: { VStack(alignment: .leading,spacing: 12) { HStack { Image(systemName: symbol).foregroundStyle(Color.mantleAccent); Spacer(); Image(systemName: "arrow.up.right").font(.caption2).foregroundStyle(.tertiary) }; HStack(alignment: .firstTextBaseline,spacing: 7) { Text("\(value)").font(.system(size: 27,weight: .medium,design: .rounded)); Text(title).font(.caption).foregroundStyle(.secondary) } }.padding(18).frame(maxWidth: .infinity,alignment: .leading).background(.primary.opacity(0.035),in: RoundedRectangle(cornerRadius: 13)).overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(.primary.opacity(0.06))) }.buttonStyle(.plain)
    }
    private func infoRow(_ title: String,_ value: String) -> some View { GridRow { Text(title).foregroundStyle(.secondary); Text(value).textSelection(.enabled) }.font(.callout) }
    @ViewBuilder private func resources(_ items: [JSON],kind: String) -> some View {
        HStack { VStack(alignment:.leading,spacing:5) { Text(kind == "skills" ? "Give it the right expertise." : "Extend what it can do.").font(.title3.weight(.semibold)); Text("Installed only for \(agent.title). Sources stay in its configuration.").font(.caption).foregroundStyle(.secondary) }; Spacer(); Button { resourceKind = kind } label: { Label("Add \(kind == "skills" ? "Skill" : "Package")",systemImage:"plus") }.buttonStyle(.borderedProminent) }
        if items.isEmpty { EmptyState(symbol:kind == "skills" ? "sparkles" : "shippingbox",title:"No \(kind) yet",detail:kind == "skills" ? "Add a skill from a Git repository or a local folder to teach this agent a repeatable workflow." : "Add a Pi package from npm or Git to give this agent new tools and extensions.").frame(height:280) }
        else { ForEach(items,id: \.self) { item in ResourceRow(agent: agent,item:item,kind:kind) } }
    }
    private var connections: some View {
        VStack(alignment:.leading,spacing:20) {
            Surface(title:"MCP servers") {
                HStack { Text("Connect this agent to external tools and services.").font(.callout).foregroundStyle(.secondary); Spacer(); Button("Configure…") { app.editingAgent = agent } }
                if agent.servers.isEmpty { Text("No MCP servers configured. Add a command or HTTP server in the configuration editor’s YAML tab.").font(.caption).foregroundStyle(.secondary) }
                ForEach(agent.servers,id: \.self) { name in let server = agent.config["mcp"]["servers"][name]; HStack { Image(systemName:"point.3.connected.trianglepath.dotted").foregroundStyle(Color.mantleAccent); VStack(alignment:.leading,spacing:4) { Text(name).font(.callout.weight(.semibold)); Text(server["url"].string.isEmpty ? server["command"].string : server["url"].string).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }; Spacer(); StatusPill(label:server["disabled"].bool ? "Disabled" : server["url"].string.isEmpty ? "Command" : "HTTP",color:server["disabled"].bool ? .secondary : .mantleAccent) } }
            }
            Surface(title:"Required secrets") { Text("Only names are stored in this configuration. Values are kept locally.").font(.caption).foregroundStyle(.secondary); ForEach(agent.config["secrets"].array,id: \.self) { secret in HStack { Label(secret.string,systemImage:"key").font(.callout.monospaced()); Spacer(); StatusPill(label:app.snapshot["secretNames"].array.contains(secret) ? "Saved locally" : "Not saved",color:app.snapshot["secretNames"].array.contains(secret) ? .green : .orange) } }; Button("Manage Secrets") { app.connectionsTab = "Secrets"; app.destination = .connections } }
            Surface(title:"Provider credentials") { Text(agent.isolated ? "This agent uses separate credentials." : "This agent uses your shared provider credentials.").font(.callout); HStack { Button(agent.isolated ? "Use Shared Logins…" : "Separate This Agent’s Logins…") { showAuthChange = true }; Button("Manage Connections") { app.connectionsTab = "Providers"; app.destination = .connections } } }
        }
    }
    private func exportConfiguration() { let panel = NSSavePanel(); panel.nameFieldStringValue = "\(agent.name).yaml"; panel.allowedContentTypes = [.yaml]; if panel.runModal() == .OK, let url = panel.url { do { try agent.yaml.write(to:url,atomically:true,encoding:.utf8); app.notice = "Configuration exported." } catch { app.error = error.localizedDescription } } }
}

extension JSON: Hashable { func hash(into hasher: inout Hasher) { hasher.combine(pretty) } }

struct ResourceRow: View {
    @EnvironmentObject private var app: AppModel
    let agent: MantleAgent; let item: JSON; let kind: String
    @State private var confirm = false
    @State private var install = false
    private var installed: Bool { agent.raw[kind == "skills" ? "installedSkills" : "installedPackages"].array.contains(item["name"]) }
    var body: some View {
        Surface {
            HStack {
                Image(systemName:kind == "skills" ? "sparkles" : "shippingbox").foregroundStyle(Color.mantleAccent)
                VStack(alignment:.leading,spacing:6) { Text(item["name"].string).font(.headline); Text(item["source"].string).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                Spacer()
                StatusPill(label:installed ? "Installed" : "Not installed",color:installed ? .green : .orange)
                if !installed { Button("Install") { install = true }.disabled(app.busy) }
                Button { confirm = true } label: { Image(systemName:"minus.circle") }.buttonStyle(.borderless).help("Remove \(item["name"].string)")
            }
        }
        .alert("Remove \(item["name"].string)?",isPresented:$confirm) {
            Button("Cancel",role:.cancel) {}
            Button("Remove",role:.destructive) { Task { await app.perform(kind + ".remove",["agent":.string(agent.name),"name":item["name"],"confirmed":.bool(true)],success:"Resource removed from this agent.") } }
        } message: { Text("This removes the installed resource and its entry from this agent’s configuration.") }
        .alert("Install \(item["name"].string)?",isPresented:$install) {
            Button("Cancel",role:.cancel) {}
            Button("Install") { Task { var parameters:[String:JSON] = ["agent":.string(agent.name),"source":item["source"],"force":.bool(true),"confirmed":.bool(true)]; parameters[kind == "skills" ? "skill" : "name"] = item["name"]; await app.perform(kind + ".add",parameters,success:"Resource installed for this agent.") } }
        } message: { Text("Install from \(item["source"].string)? Only install resources you trust. Existing files for this resource will be replaced.") }
    }
}

struct AddResourceSheet: View {
    @EnvironmentObject private var app: AppModel
    @Environment(\.dismiss) private var dismiss
    let agent: MantleAgent; let kind: String
    @State private var source = ""; @State private var name = ""
    @State private var saving = false; @State private var failure: String?
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            PageHeader(title:kind == "skills" ? "Add a Skill" : "Add a Package",subtitle:"For \(agent.title)",symbol:kind == "skills" ? "sparkles" : "shippingbox")
            Form { TextField("Source",text:$source,prompt:Text(kind == "skills" ? "https://github.com/owner/skills" : "npm:package-name@version")); TextField(kind == "skills" ? "Skill name" : "Package name",text:$name,prompt:Text("Optional")) }.formStyle(.grouped)
            Text(kind == "skills" ? "Use a Git URL or an absolute local folder path. If a repository contains several skills, enter the skill name." : "Use an npm package or Git URL. Pin a version in the source to keep installs predictable.").font(.caption).foregroundStyle(.secondary)
            Text("Only install resources from sources you trust. Packages can run code on your Mac.").font(.caption).foregroundStyle(.secondary)
            if let failure { Text(failure).foregroundStyle(.red).font(.caption).textSelection(.enabled) }
            HStack { if saving { ProgressView().controlSize(.small); Text("Installing…").font(.caption) }; Spacer(); Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).disabled(saving); Button("Add \(kind == "skills" ? "Skill" : "Package")") { Task { await add() } }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).disabled(source.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty || saving) }
        }.padding(28).frame(width:570).interactiveDismissDisabled(saving)
    }
    private func add() async { saving = true; defer { saving = false }; do { var p:[String:JSON] = ["agent":.string(agent.name),"source":.string(source.trimmingCharacters(in:.whitespacesAndNewlines))]; if !name.isEmpty { p[kind == "skills" ? "skill" : "name"] = .string(name) }; _ = try await app.request(kind + ".add",p); await app.refresh(); dismiss() } catch { failure = error.localizedDescription } }
}
