import SwiftUI
import AppKit

private struct ManagementLayout<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 24) { content }.padding(32).frame(maxWidth: 1100).frame(maxWidth: .infinity) }
    }
}

private struct PendingAction: Identifiable {
    let id = UUID()
    var title: String
    var message: String
    var button: String
    var operation: String
    var parameters: [String: JSON]
    var success: String
    var destructive = false
}

struct RegistryView: View {
    @EnvironmentObject var app: AppModel
    @State private var address = ""
    @State private var query = ""
    @State private var agents: [JSON] = []
    @State private var selected = ""
    @State private var detail: JSON = .null
    @State private var revisions: [JSON] = []
    @State private var revision = 0
    @State private var loading = false
    @State private var connected = false
    @State private var connectionError: String?
    @State private var pending: PendingAction?
    @State private var editor = false
    @State private var source = ""
    @State private var remoteName = ""
    @State private var diff = ""
    @State private var showDiff = false
    @State private var localDigest = ""
    @State private var publishName = ""
    @State private var showToken = false
    @State private var allowUntrustedCertificate = false
    @State private var showSource = false
    @State private var sortRecent = true
    @State private var hostLocally = false
    @State private var port = "3100"
    @State private var imageVersion = "0.1"
    @State private var serverLogs = ""
    @State private var showLogs = false

    private var filtered: [JSON] {
        agents.filter { query.isEmpty || ($0["name"].string + " " + $0["description"].string).localizedCaseInsensitiveContains(query) }.sorted {
            sortRecent ? $0["updatedAt"].string > $1["updatedAt"].string : $0["name"].string < $1["name"].string
        }
    }
    private var localExists: Bool { app.agents.contains { $0.name == selected } }
    private var config: JSON { detail["config"] }

    var body: some View {
        ManagementLayout {
            PageHeader(title: "Registry", subtitle: "Your agents, available wherever you work.", symbol: "network")
            connectionPanel
            localHostingPanel
            if connected {
                libraryPanel
                if !selected.isEmpty && detail != .null { agentPanel }
                publishPanel
            } else if !loading {
                EmptyState(symbol: "point.3.connected.trianglepath.dotted", title: "Connect your registry", detail: "Connect to a Mantle Cloud instance to browse configurations, publish your agents, and bring them to this Mac.")
            }
        }
        .task { address = app.cloudURL; allowUntrustedCertificate = app.snapshot["settings"]["cloud_insecure"].bool; if !address.isEmpty { await reload() } }
        .onChange(of: address) { _, value in
            if !app.registryToken.isEmpty && AppModel.registryOrigin(value) != AppModel.registryOrigin(app.registryTokenURL) {
                app.registryToken = ""; app.registryTokenURL = ""
            }
        }
        .alert(item: $pending) { item in
            Alert(title: Text(item.title), message: Text(item.message), primaryButton: item.destructive ? .destructive(Text(item.button), action: { run(item) }) : .default(Text(item.button), action: { run(item) }), secondaryButton: .cancel())
        }
        .sheet(isPresented: $editor) { RegistryEditor(name: remoteName, initial: source, expectedRevision: remoteName.isEmpty ? nil : detail["revision"]["number"].int) { await reload(); if !selected.isEmpty { await load(selected) } }.environmentObject(app) }
        .sheet(isPresented: $showLogs) {
            VStack(alignment: .leading, spacing: 18) {
                Text("Local registry logs").font(.title2.bold())
                ScrollView([.horizontal, .vertical]) { Text(serverLogs).font(.system(size: 12, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                HStack { Spacer(); Button("Done") { showLogs = false }.keyboardShortcut(.defaultAction) }
            }.padding(24).frame(width: 750, height: 500)
        }
        .sheet(isPresented: $showDiff) {
            VStack(alignment: .leading, spacing: 18) {
                Text("Local and registry changes").font(.title2.bold())
                Text(selected).foregroundStyle(.secondary)
                ScrollView([.horizontal, .vertical]) { Text(diff.isEmpty ? "The local and registry configurations match." : diff).font(.system(.body, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(16) }.background(Color.mantleCanvas, in: RoundedRectangle(cornerRadius: 10))
                HStack { Spacer(); Button("Done") { showDiff = false }.keyboardShortcut(.defaultAction) }
            }.padding(24).frame(width: 760, height: 560)
        }
    }

    private var connectionPanel: some View {
        Surface {
            HStack {
                Label("Connection", systemImage: "link").font(.headline)
                Spacer()
                if loading { ProgressView().controlSize(.small) }
                StatusPill(label: connected ? "Connected" : "Not connected", color: connected ? .green : .secondary)
                if !app.cloudURL.isEmpty {
                    Menu { Button("Disconnect…") { pending = PendingAction(title: "Disconnect this registry?", message: "The saved registry address is cleared. Your local agents and published configurations are kept.", button: "Disconnect", operation: "cloud.disconnect", parameters: ["confirmed": .bool(true)], success: "Registry disconnected.") } } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 24)
                }
            }
            HStack(spacing: 12) {
                TextField("https://registry.example.com", text: $address).textFieldStyle(.roundedBorder).accessibilityLabel("Registry address").onSubmit { connect() }
                Button(connected && address == app.cloudURL ? "Reconnect" : "Connect") { connect() }.buttonStyle(.borderedProminent).disabled(address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || loading || app.busy)
                if connected { Button { Task { await reload() } } label: { Image(systemName: "arrow.clockwise") }.help("Refresh registry").disabled(loading) }
            }
            DisclosureGroup("Connection options", isExpanded: $showToken) {
                VStack(alignment: .leading, spacing: 8) {
                    SecureField("Bearer token", text: Binding(get: { app.registryToken }, set: { app.registryToken = $0; app.registryTokenURL = address })).textFieldStyle(.roundedBorder)
                    Text("For registries behind an authenticating gateway. Kept only for this app session; use HTTPS when sending a token.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Allow an untrusted TLS certificate", isOn: $allowUntrustedCertificate).toggleStyle(.checkbox)
                    if allowUntrustedCertificate { Text("Certificate verification will be disabled for this registry.").font(.caption).foregroundStyle(.orange) }
                }.padding(.top, 8)
            }.font(.callout)
            if let connectionError { Label(connectionError, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            if connected { Text("Configurations are shared with everyone who can access this registry. Agent sessions and credential values stay on this Mac.").font(.caption).foregroundStyle(.secondary) }
        }
    }

    private var localHostingPanel: some View {
        DisclosureGroup("Host a registry on this Mac", isExpanded: $hostLocally) {
            Surface {
                Text("Run your own registry with Docker Compose. Configurations stay in a persistent local volume.").font(.callout).foregroundStyle(.secondary)
                HStack(spacing: 14) {
                    TextField("Port", text: $port).textFieldStyle(.roundedBorder).frame(width: 100)
                    TextField("Version", text: $imageVersion).textFieldStyle(.roundedBorder).frame(width: 140)
                    Spacer()
                    Button("View logs") { Task { do { serverLogs = try await app.request("cloud.logs")["logs"].string; showLogs = true } catch { app.error = error.localizedDescription } } }
                    Button("Stop…") { pending = PendingAction(title: "Stop the local registry?", message: "Registry access will be unavailable until it is started again. Published configurations are kept.", button: "Stop registry", operation: "cloud.stop", parameters: ["confirmed": .bool(true)], success: "Local registry stopped.") }
                    Button("Start registry") { pending = PendingAction(title: "Start a local Mantle registry?", message: "Docker Compose will download and start Mantle Cloud, then connect this app to it. Docker must already be running.", button: "Start registry", operation: "cloud.start", parameters: ["port": .number(Double(Int(port) ?? 3100)), "version": .string(imageVersion), "confirmed": .bool(true)], success: "Local registry started.") }.disabled(Int(port) == nil || imageVersion.isEmpty)
                }.disabled(app.busy)
            }.padding(.top, 12)
        }.font(.callout).foregroundStyle(.secondary)
    }

    private var libraryPanel: some View {
        Surface {
            HStack {
                Text("Available agents").font(.headline)
                StatusPill(label: "\(agents.count)")
                Spacer()
                Button("New configuration", systemImage: "plus") { remoteName = ""; source = "mantle: \"0.1\"\nagent:\n  name: new-agent\n  description: A focused personal agent\nruntime:\n  name: pi\n  version: latest\n"; editor = true }
            }
            HStack {
                TextField("Search agents", text: $query).textFieldStyle(.roundedBorder)
                Picker("Sort", selection: $sortRecent) { Text("Recent").tag(true); Text("Name").tag(false) }.labelsHidden().frame(width: 120)
            }
            if filtered.isEmpty {
                Text(agents.isEmpty ? "No configurations have been published yet. Publish one of your local agents below." : "No agents match your search.").foregroundStyle(.secondary).padding(.vertical, 12)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], spacing: 12) {
                    ForEach(filtered, id: \.self) { agent in
                        Button { Task { await load(agent["name"].string) } } label: {
                            VStack(alignment: .leading, spacing: 10) {
                                HStack { Image(systemName: "square.stack.3d.up").foregroundStyle(Color.mantleAccent); Spacer(); Text("r\(agent["revision"].int)").font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
                                Text(agent["name"].string).font(.headline).lineLimit(1)
                                Text(agent["description"].string.isEmpty ? "No description" : agent["description"].string).font(.callout).foregroundStyle(.secondary).lineLimit(2).frame(height: 34, alignment: .top)
                                Text("\(agent["skillCount"].int) skills · \(agent["packageCount"].int) packages").font(.caption).foregroundStyle(.secondary)
                            }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(selected == agent["name"].string ? Color.mantleAccent.opacity(0.12) : Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10)).overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(selected == agent["name"].string ? Color.mantleAccent.opacity(0.65) : Color.clear))
                        }.buttonStyle(.plain).accessibilityLabel("Open \(agent["name"].string)")
                    }
                }
            }
        }
    }

    private var agentPanel: some View {
        Surface {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) { Text(selected).font(.title2.bold()); Text(detail["description"].string).foregroundStyle(.secondary) }
                Spacer()
                if revision > 0 { StatusPill(label: "Historical · r\(revision)", color: .orange) } else { StatusPill(label: localExists ? "On this Mac" : "Registry only") }
                Menu {
                    Button("Edit latest configuration") { Task { do { let current = try await app.request("cloud.latest", ["name": .string(selected)]); detail = current; revision = 0; source = current["yaml"].string; remoteName = selected; editor = true } catch { app.error = error.localizedDescription } } }
                    Button("Copy YAML") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(detail["yaml"].string, forType: .string) }
                    Divider()
                    Button("Delete from registry…", role: .destructive) {
                        pending = PendingAction(title: "Delete \(selected) from the registry?", message: "This permanently deletes every registry revision. The agent on this Mac is kept.", button: "Delete", operation: "cloud.delete", parameters: ["name": .string(selected), "confirmed": .bool(true)], success: "Registry configuration deleted.", destructive: true)
                    }
                } label: { Image(systemName: "ellipsis.circle") }.menuStyle(.borderlessButton).frame(width: 28)
            }
            HStack(spacing: 22) {
                Label(config["runtime"]["name"].string + " " + config["runtime"]["version"].string, systemImage: "shippingbox")
                Label(config["model"]["name"].string, systemImage: "sparkles")
                Label("\(config["resources"]["skills"].array.count) skills", systemImage: "square.stack")
            }.font(.callout).foregroundStyle(.secondary)
            HStack(spacing: 10) {
                if localExists {
                    Button("Compare changes", systemImage: "arrow.left.arrow.right") { Task { do { let value = try await app.request("cloud.diff", ["name": .string(selected)]); diff = value["diff"].string; localDigest = value["localDigest"].string; showDiff = true } catch { app.error = error.localizedDescription } } }
                    Button("Update local agent", systemImage: "arrow.down.circle") {
                        pending = PendingAction(title: "Update \(selected) from the registry?", message: "The latest registry revision replaces local configuration and installed resources. Local sessions, context, and authentication are preserved. Compare changes before replacing local edits.", button: "Update", operation: "cloud.update", parameters: updateParameters, success: "Agent updated from the registry.")
                    }.buttonStyle(.borderedProminent)
                } else {
                    Button("Add to this Mac", systemImage: "arrow.down.circle") {
                        pending = PendingAction(title: "Add \(selected) to this Mac?", message: "Mantle will download the latest configuration and install its declared skills, packages, and runtime. Review the sources below before installing.", button: "Add agent", operation: "cloud.pull", parameters: ["name": .string(selected), "confirmed": .bool(true)], success: "Agent added to this Mac.")
                    }.buttonStyle(.borderedProminent)
                }
                if revision > 0 { Text("Installs the latest revision.").font(.caption).foregroundStyle(.secondary) }
            }.disabled(app.busy)
            Divider()
            if !config["resources"]["skills"].array.isEmpty || !config["resources"]["packages"].array.isEmpty { resourceSources }
            HStack {
                Text("Configuration").font(.headline)
                Spacer()
                Picker("Revision", selection: $revision) {
                    Text("Latest").tag(0)
                    ForEach(revisions, id: \.self) { r in Text("r\(r["number"].int) · \(shortDate(r["createdAt"].string))").tag(r["number"].int) }
                }.frame(maxWidth: 290).onChange(of: revision) { _, value in Task { await loadRevision(value) } }
            }
            DisclosureGroup("View agent.yaml", isExpanded: $showSource) {
                ScrollView([.horizontal, .vertical]) { Text(detail["yaml"].string).font(.system(size: 12, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(14) }.frame(minHeight: 160, maxHeight: 360).background(Color.mantleCanvas, in: RoundedRectangle(cornerRadius: 8)).padding(.top, 8)
            }
            if !config["secrets"].array.isEmpty { Text("Requires local secrets: " + config["secrets"].array.map(\.string).joined(separator: ", ")).font(.caption).foregroundStyle(.secondary) }
        }
    }

    private var updateParameters: [String: JSON] {
        var result: [String: JSON] = ["name": .string(selected), "confirmed": .bool(true)]
        if let latest = revisions.first { result["expectedRevision"] = latest["number"] }
        if !localDigest.isEmpty { result["expectedDigest"] = .string(localDigest) }
        return result
    }

    private var resourceSources: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Declared sources").font(.subheadline.weight(.semibold))
            ForEach(config["resources"]["skills"].array + config["resources"]["packages"].array, id: \.self) { resource in
                HStack(alignment: .top) { Text(resource["name"].string).font(.callout).frame(width: 150, alignment: .leading); Text(resource["source"].string).font(.system(.caption, design: .monospaced)).foregroundStyle(.secondary).textSelection(.enabled); Spacer() }
            }
        }
    }

    private var publishPanel: some View {
        Surface(title: "Publish from this Mac") {
            Text("Share an agent’s configuration as an immutable registry revision.").font(.callout).foregroundStyle(.secondary)
            HStack {
                Picker("Agent", selection: $publishName) { Text("Choose an agent").tag(""); ForEach(app.agents) { agent in Text(agent.name).tag(agent.name) } }.frame(maxWidth: 420)
                Spacer()
                Button("Publish", systemImage: "arrow.up.circle") {
                    pending = PendingAction(title: "Publish \(publishName)?", message: "Its agent.yaml will be visible to everyone with access to this registry. Existing registry content receives a new revision. Credentials and sessions are not uploaded.", button: "Publish", operation: "cloud.push", parameters: ["name": .string(publishName), "confirmed": .bool(true)], success: "Agent published.")
                }.buttonStyle(.borderedProminent).disabled(publishName.isEmpty || app.busy)
            }
        }
    }

    private func connect() {
        let parameters: [String: JSON] = ["url": .string(address.trimmingCharacters(in: .whitespacesAndNewlines)), "insecure": .bool(allowUntrustedCertificate), "confirmed": .bool(true)]
        if allowUntrustedCertificate {
            pending = PendingAction(title: "Connect without verifying the certificate?", message: "Only continue for a registry you control. Mantle cannot verify the server’s identity at " + address, button: "Connect", operation: "cloud.connect", parameters: parameters, success: "Registry connected.")
            return
        }
        Task {
            loading = true
            do {
                _ = try await app.request("cloud.connect", parameters)
                await app.refresh(); address = app.cloudURL
                await reload(); app.notice = "Registry connected."
            } catch { connectionError = error.localizedDescription; connected = false }
            loading = false
        }
    }
    private func run(_ action: PendingAction) {
        Task {
            if await app.perform(action.operation, action.parameters, success: action.success) != nil {
                if action.operation == "cloud.disconnect" {
                    selected = ""; detail = .null; agents = []; address = ""; connected = false; connectionError = nil
                    app.registryToken = ""; app.registryTokenURL = ""; return
                }
                if action.operation == "cloud.delete" { selected = ""; detail = .null }
                address = app.cloudURL
                await reload()
                if !selected.isEmpty { await load(selected) }
            }
        }
    }
    private func reload() async {
        loading = true; defer { loading = false }
        do { let value = try await app.request("cloud.list"); agents = value.array; connected = true; connectionError = nil }
        catch { connected = false; connectionError = error.localizedDescription }
    }
    private func load(_ name: String) async {
        loading = true; defer { loading = false }
        do {
            async let current = app.request("cloud.latest", ["name": .string(name)])
            async let history = app.request("cloud.history", ["name": .string(name)])
            detail = try await current; revisions = try await history.array; selected = name; revision = 0; localDigest = ""
        } catch { app.error = error.localizedDescription }
    }
    private func loadRevision(_ number: Int) async {
        guard !selected.isEmpty else { return }
        do { detail = try await app.request(number == 0 ? "cloud.latest" : "cloud.revision", number == 0 ? ["name": .string(selected)] : ["name": .string(selected), "revision": .number(Double(number))]) } catch { app.error = error.localizedDescription }
    }
}

private func shortDate(_ value: String) -> String {
    let parser = ISO8601DateFormatter(); parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = parser.date(from: value) ?? ISO8601DateFormatter().date(from: value) { return date.formatted(date: .abbreviated, time: .omitted) }
    return String(value.prefix(10))
}

private struct RegistryEditor: View {
    @EnvironmentObject var app: AppModel
    @Environment(\.dismiss) private var dismiss
    let name: String
    let initial: String
    let expectedRevision: Int?
    let saved: () async -> Void
    @State private var yaml = ""
    @State private var output = ""
    @State private var verified = false
    @State private var showPublish = false
    @State private var working = false
    @State private var publishName = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { VStack(alignment: .leading, spacing: 5) { Text(name.isEmpty ? "New registry configuration" : "Edit \(name)").font(.title2.bold()); Text("Saving creates a new immutable revision.").foregroundStyle(.secondary) }; Spacer() }
            if name.isEmpty { Text("Set agent.name below, then validate before saving.").font(.callout).foregroundStyle(.secondary) }
            TextEditor(text: $yaml).font(.system(size: 13, design: .monospaced)).scrollContentBackground(.hidden).padding(12).background(Color.mantleCanvas, in: RoundedRectangle(cornerRadius: 10)).onChange(of: yaml) { _, _ in verified = false }.accessibilityLabel("Agent YAML configuration")
            if !output.isEmpty { Text(output).font(.callout).foregroundStyle(verified ? Color.green : Color.secondary).lineLimit(5).textSelection(.enabled) }
            HStack {
                Button("Validate") { validate(format: false) }
                Button("Format") { validate(format: true) }
                if working { ProgressView().controlSize(.small) }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Save revision") { showPublish = true }.buttonStyle(.borderedProminent).disabled(!verified || publishName.isEmpty).keyboardShortcut("s", modifiers: .command)
            }.disabled(working || app.busy)
        }.padding(24).frame(width: 800, height: 650)
        .onAppear { yaml = initial; publishName = name }
        .confirmationDialog("Publish this configuration to the registry?", isPresented: $showPublish, titleVisibility: .visible) {
            Button("Save revision") { save() }
        } message: { Text("Anyone with access to the registry can read the complete YAML. Include secret names only, never credential values.") }
    }
    private func save() {
        Task {
            do {
                if name.isEmpty {
                    let current = try await app.request("cloud.list")
                    guard !current.array.contains(where: { $0["name"].string == publishName }) else {
                        output = "This agent already exists. Open its existing configuration to add a revision."
                        return
                    }
                }
                var parameters: [String: JSON] = ["name": .string(publishName), "yaml": .string(yaml), "confirmed": .bool(true)]
                if let expectedRevision { parameters["expectedRevision"] = .number(Double(expectedRevision)) }
                if await app.perform("cloud.save", parameters, success: "Registry revision saved.") != nil { await saved(); dismiss() }
            } catch { output = error.localizedDescription }
        }
    }
    private func validate(format: Bool) {
        Task {
            working = true; defer { working = false }
            do {
                var parameters: [String: JSON] = ["yaml": .string(yaml)]
                if !name.isEmpty { parameters["name"] = .string(name) }
                let value = try await app.request(format ? "agent.format" : "agent.validate", parameters)
                publishName = value["config"]["agent"]["name"].string
                if format && !value["yaml"].string.isEmpty { yaml = value["yaml"].string }
                verified = true; output = "Valid Mantle configuration."
            } catch { verified = false; output = error.localizedDescription }
        }
    }
}

struct ConnectionsView: View {
    @EnvironmentObject var app: AppModel
    @State private var search = ""
    @State private var selectedProvider: String?
    @State private var customProvider = false
    @State private var showSecret = false
    @State private var selectedSecret = ""
    @State private var pending: PendingAction?
    private var providers: [JSON] {
        let known = Set(app.providers.map { $0["id"].string })
        let custom = app.credentials.filter { !known.contains($0["provider"].string) }.map { JSON.object(["id": $0["provider"], "name": $0["provider"], "api_key": .bool(true), "oauth": .bool(false)]) }
        return (app.providers + custom).filter { search.isEmpty || ($0["name"].string + $0["id"].string).localizedCaseInsensitiveContains(search) }
    }
    private var secrets: [String] { app.snapshot["secretNames"].array.map(\.string).sorted() }
    var body: some View {
        ManagementLayout {
            PageHeader(title: "Connections", subtitle: "Sign in once. Bring the right intelligence to every agent.", symbol: "key.horizontal")
            Picker("Connection type", selection: $app.connectionsTab) { Text("Model Providers").tag("Providers"); Text("Secrets").tag("Secrets") }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 360)
            if app.connectionsTab == "Providers" {
            Surface {
                HStack { Text("Model providers").font(.headline); Spacer(); Button("Custom provider", systemImage: "plus") { customProvider = true } }
                Text("Shared logins are available to your agents. An agent can use its own login from its settings.").font(.callout).foregroundStyle(.secondary)
                TextField("Find a provider", text: $search).textFieldStyle(.roundedBorder)
                VStack(spacing: 0) {
                    ForEach(providers, id: \.self) { provider in providerRow(provider); if provider != providers.last { Divider().padding(.leading, 44) } }
                }
                if providers.isEmpty { Text("No matching providers.").foregroundStyle(.secondary).padding(.vertical, 12) }
            }
            } else {
            Surface {
                HStack { Text("Local secrets").font(.headline); Spacer(); Button("Add secret", systemImage: "plus") { selectedSecret = ""; showSecret = true } }
                Text("Store values required by skills and MCP servers. Only declared secret names are shared with a registry.").font(.callout).foregroundStyle(.secondary)
                if secrets.isEmpty { Text("No local secrets saved yet.").foregroundStyle(.secondary).padding(.vertical, 10) }
                ForEach(secrets, id: \.self) { name in
                    HStack {
                        Image(systemName: "lock.fill").foregroundStyle(.secondary)
                        Text(name).font(.system(.callout, design: .monospaced))
                        Spacer(); StatusPill(label: "Saved", color: .green)
                        Button("Replace…") { selectedSecret = name; showSecret = true }
                        Menu { Button("Remove secret…", role: .destructive) { pending = PendingAction(title: "Remove \(name)?", message: "Agents that require this secret will need a new value before they can use it.", button: "Remove", operation: "secrets.remove", parameters: ["name": .string(name), "confirmed": .bool(true)], success: "Secret removed.", destructive: true) } } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 24)
                    }.padding(.vertical, 6)
                }
            }
            }
        }
        .sheet(isPresented: Binding(get: { selectedProvider != nil }, set: { if !$0 { selectedProvider = nil } })) { CredentialSheet(provider: selectedProvider ?? "").environmentObject(app) }
        .sheet(isPresented: $customProvider) { CredentialSheet(provider: "").environmentObject(app) }
        .sheet(isPresented: $showSecret) { SecretSheet(name: selectedSecret).environmentObject(app) }
        .alert(item: $pending) { item in Alert(title: Text(item.title), message: Text(item.message), primaryButton: .destructive(Text(item.button), action: { Task { await app.perform(item.operation, item.parameters, success: item.success) } }), secondaryButton: .cancel()) }
    }
    private func providerRow(_ provider: JSON) -> some View {
        let id = provider["id"].string
        let credential = app.credentials.first { $0["provider"].string == id }
        return HStack(spacing: 12) {
            Image(systemName: "sparkle").font(.system(size: 17)).foregroundStyle(Color.mantleAccent).frame(width: 32, height: 32).background(Color.mantleAccent.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(provider["name"].string).font(.callout.weight(.medium))
                Text(credential == nil ? id : (credential?["type"].string == "oauth" ? "Signed in with account" : "API key saved")).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if credential != nil { StatusPill(label: credential?["type"].string == "oauth" ? "Signed in" : "Key saved", color: .green) }
            if provider["oauth"].bool {
                Button(credential == nil ? "Sign in" : "Sign in again") { app.terminal(["auth", "login", id, "--oauth"]) }.help("Complete account sign-in in Terminal, then refresh Mantle")
            }
            if provider["api_key"].bool { Button(credential == nil ? "Add key" : "Replace key") { selectedProvider = id } }
            if credential != nil {
                Menu { Button("Sign out…", role: .destructive) { pending = PendingAction(title: "Sign out of \(provider["name"].string)?", message: "Agents using this shared login will need you to sign in again.", button: "Sign out", operation: "auth.logout", parameters: ["provider": .string(id), "confirmed": .bool(true)], success: "Provider signed out.", destructive: true) } } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 24)
            }
        }.padding(.vertical, 12).disabled(app.busy)
    }
}

private struct CredentialSheet: View {
    @EnvironmentObject var app: AppModel
    @Environment(\.dismiss) private var dismiss
    let provider: String
    @State private var providerID = ""
    @State private var key = ""
    @State private var replace = false
    private var existing: Bool { app.credentials.contains { $0["provider"].string == providerID } }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Connect a provider", subtitle: "Your API key is saved in Mantle’s local credential store.", symbol: "key.horizontal")
            VStack(alignment: .leading, spacing: 8) {
                Text("Provider").font(.headline)
                if provider.isEmpty { TextField("Provider ID", text: $providerID).textFieldStyle(.roundedBorder) } else { Text(app.providers.first { $0["id"].string == provider }?["name"].string ?? provider) }
            }
            VStack(alignment: .leading, spacing: 8) { Text("API key").font(.headline); SecureField("Paste your API key", text: $key).textFieldStyle(.roundedBorder) }
            Text("Available to agents using shared logins. Keys are never included in registry uploads.").font(.callout).foregroundStyle(.secondary)
            HStack { Spacer(); Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction); Button("Save key") { if existing { replace = true } else { save() } }.buttonStyle(.borderedProminent).disabled(key.isEmpty || providerID.isEmpty || app.busy).keyboardShortcut(.defaultAction) }
        }.padding(28).frame(width: 520).onAppear { providerID = provider }
        .confirmationDialog("Replace the saved key for \(providerID)?", isPresented: $replace, titleVisibility: .visible) { Button("Replace key") { save() } }
    }
    private func save() { Task { if await app.perform("auth.saveKey", ["provider": .string(providerID), "key": .string(key), "confirmed": .bool(true)], success: "Provider key saved.") != nil { key = ""; dismiss() } } }
}

private struct SecretSheet: View {
    @EnvironmentObject var app: AppModel
    @Environment(\.dismiss) private var dismiss
    let name: String
    @State private var secretName = ""
    @State private var value = ""
    @State private var replace = false
    private var existing: Bool { app.snapshot["secretNames"].array.map(\.string).contains(secretName) }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: name.isEmpty ? "Add a local secret" : "Replace local secret", subtitle: "Store the value on this Mac for agents that declare its name.", symbol: "lock")
            VStack(alignment: .leading, spacing: 8) { Text("Name").font(.headline); TextField("SERVICE_API_KEY", text: $secretName).textFieldStyle(.roundedBorder).disabled(!name.isEmpty) }
            VStack(alignment: .leading, spacing: 8) { Text("Value").font(.headline); SecureField("Secret value", text: $value).textFieldStyle(.roundedBorder) }
            HStack { Spacer(); Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction); Button("Save secret") { if existing { replace = true } else { save() } }.buttonStyle(.borderedProminent).disabled(secretName.isEmpty || value.isEmpty || app.busy).keyboardShortcut(.defaultAction) }
        }.padding(28).frame(width: 520).onAppear { secretName = name }
        .confirmationDialog("Replace the saved value of \(secretName)?", isPresented: $replace, titleVisibility: .visible) { Button("Replace value") { save() } }
    }
    private func save() { Task { if await app.perform("secrets.set", ["name": .string(secretName), "value": .string(value), "confirmed": .bool(true)], success: "Local secret saved.") != nil { value = ""; dismiss() } } }
}

struct RuntimesView: View {
    @EnvironmentObject var app: AppModel
    @State private var version = "latest"
    @State private var makeDefault = true
    @State private var pending: PendingAction?
    var body: some View {
        ManagementLayout {
            PageHeader(title: "Runtimes", subtitle: "A shared engine. An independent workspace for every agent.", symbol: "shippingbox")
            Surface(title: "Install Pi") {
                Text("Choose a version or use latest. Mantle installs it alongside your existing runtimes.").font(.callout).foregroundStyle(.secondary)
                HStack(spacing: 14) {
                    TextField("Version or npm tag", text: $version).textFieldStyle(.roundedBorder).frame(maxWidth: 260)
                    Toggle("Use as default", isOn: $makeDefault).toggleStyle(.checkbox)
                    Spacer()
                    Button("Install runtime", systemImage: "arrow.down.circle") { pending = PendingAction(title: "Install Pi \(version)?", message: "Mantle will download this runtime and its dependencies.\(makeDefault ? " Agents configured for latest will use it as their default." : "")", button: "Install", operation: "runtime.install", parameters: ["version": .string(version), "makeDefault": .bool(makeDefault), "confirmed": .bool(true)], success: "Runtime installed.") }.buttonStyle(.borderedProminent).disabled(version.isEmpty || app.busy)
                }
            }
            Surface {
                HStack { Text("Installed runtimes").font(.headline); Spacer(); StatusPill(label: "\(app.runtimes.count)") }
                if app.runtimes.isEmpty { EmptyState(symbol: "shippingbox", title: "Ready for your first runtime", detail: "Install Pi to run agents on this Mac.") }
                ForEach(app.runtimes, id: \.self) { runtime in runtimeRow(runtime); if runtime != app.runtimes.last { Divider() } }
            }
            Surface(title: "How versions work") {
                Text("Agents with a pinned runtime keep their chosen version. Agents configured for latest use your default runtime. An installed version cannot be removed while an agent depends on it.").font(.callout).foregroundStyle(.secondary)
            }
        }
        .alert(item: $pending) { item in Alert(title: Text(item.title), message: Text(item.message), primaryButton: item.destructive ? .destructive(Text(item.button), action: { perform(item) }) : .default(Text(item.button), action: { perform(item) }), secondaryButton: .cancel()) }
    }
    private func runtimeRow(_ runtime: JSON) -> some View {
        let version = runtime["version"].string
        let users = runtime["users"].array.map(\.string)
        return HStack(spacing: 14) {
            Image(systemName: "shippingbox.fill").font(.system(size: 24)).foregroundStyle(Color.mantleAccent).frame(width: 44, height: 44).background(Color.mantleAccent.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            VStack(alignment: .leading, spacing: 5) {
                HStack { Text("Pi \(version)").font(.headline); if runtime["default"].bool { StatusPill(label: "Default") } }
                Text(users.isEmpty ? "No agents depend on this version" : "Used by " + users.joined(separator: ", ")).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if !runtime["default"].bool { Button("Make default") { pending = PendingAction(title: "Use Pi \(version) by default?", message: "Agents configured for latest will use this version. Pinned agents keep their current runtime.", button: "Make default", operation: "runtime.use", parameters: ["version": .string(version), "confirmed": .bool(true)], success: "Default runtime updated.") } }
            Menu { Button("Remove runtime…", role: .destructive) { pending = PendingAction(title: "Remove Pi \(version)?", message: "This installed runtime will be removed from this Mac. You can install it again later.", button: "Remove", operation: "runtime.remove", parameters: ["version": .string(version), "confirmed": .bool(true)], success: "Runtime removed.", destructive: true) }.disabled(!users.isEmpty) } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 24)
        }.padding(.vertical, 8).disabled(app.busy)
    }
    private func perform(_ item: PendingAction) { Task { await app.perform(item.operation, item.parameters, success: item.success) } }
}

struct HealthView: View {
    @EnvironmentObject var app: AppModel
    @State private var checks: [JSON] = []
    @State private var loading = false
    @State private var includeRegistry = true
    @State private var lastChecked: Date?
    private var groups: [String] { var values: [String] = []; for check in checks { let group = check["group"].string; if !values.contains(group) { values.append(group) } }; return values }
    private var issues: Int { checks.filter { ["fail", "warn"].contains($0["status"].string) }.count }
    var body: some View {
        ManagementLayout {
            PageHeader(title: "Health", subtitle: "A clear view of what’s ready and what needs attention.", symbol: "heart.text.square")
            Surface {
                HStack(spacing: 16) {
                    Image(systemName: checks.isEmpty ? "stethoscope" : issues == 0 ? "checkmark.circle.fill" : "exclamationmark.circle.fill").font(.system(size: 34)).foregroundStyle(checks.isEmpty ? Color.mantleAccent : issues == 0 ? .green : .orange)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(checks.isEmpty ? "Check your environment" : issues == 0 ? "Everything looks ready" : "\(issues) item\(issues == 1 ? "" : "s") need\(issues == 1 ? "s" : "") attention").font(.title3.weight(.semibold))
                        if let lastChecked { Text("Checked \(lastChecked.formatted(date: .omitted, time: .shortened))").font(.callout).foregroundStyle(.secondary) } else { Text("Review dependencies, credentials, agents, and registry access.").font(.callout).foregroundStyle(.secondary) }
                    }
                    Spacer()
                    if loading { ProgressView().controlSize(.small) }
                    Button("Run checks", systemImage: "arrow.clockwise") { Task { await run() } }.buttonStyle(.borderedProminent).disabled(loading)
                }
                Toggle("Include registry connection", isOn: $includeRegistry).toggleStyle(.checkbox)
            }
            ForEach(groups, id: \.self) { group in
                Surface(title: group) {
                    ForEach(checks.filter { $0["group"].string == group }, id: \.self) { check in
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: symbol(check["status"].string)).foregroundStyle(color(check["status"].string)).frame(width: 22)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(check["name"].string).font(.callout.weight(.semibold))
                                Text(check["detail"].string).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                                if !check["fix"].string.isEmpty { Text(check["fix"].string).font(.caption).foregroundStyle(Color.mantleAccent).textSelection(.enabled) }
                            }
                            Spacer()
                        }.padding(.vertical, 4)
                    }
                }
            }
        }.task { if checks.isEmpty { await run() } }
    }
    private func run() async {
        loading = true; defer { loading = false }
        do {
            let authenticatedRegistry = includeRegistry && !app.registryToken.isEmpty
            checks = try await app.request("doctor", ["cloud": .bool(includeRegistry && !authenticatedRegistry)]).array
            if authenticatedRegistry {
                do {
                    let result = try await app.request("cloud.status")
                    checks.append(.object(["group": .string("Cloud"), "name": .string("Mantle Cloud"), "status": .string("ok"), "detail": result["url"], "fix": .string("")]))
                } catch {
                    checks.append(.object(["group": .string("Cloud"), "name": .string("Mantle Cloud"), "status": .string("fail"), "detail": .string(error.localizedDescription), "fix": .string("Review your registry address and gateway token in Registry.")]))
                }
            }
            lastChecked = Date()
        } catch { app.error = error.localizedDescription }
    }
    private func color(_ status: String) -> Color { switch status { case "ok": return .green; case "fail": return .red; case "warn": return .orange; default: return .mantleAccent } }
    private func symbol(_ status: String) -> String { switch status { case "ok": return "checkmark.circle.fill"; case "fail": return "xmark.circle.fill"; case "warn": return "exclamationmark.triangle.fill"; default: return "info.circle" } }
}
