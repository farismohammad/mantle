import Foundation
import SwiftUI
import AppKit
import Security

indirect enum JSON: Codable, Equatable {
    case object([String: JSON]), array([JSON]), string(String), number(Double), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([JSON].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSON].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self { case .object(let v): try c.encode(v); case .array(let v): try c.encode(v); case .string(let v): try c.encode(v); case .number(let v): try c.encode(v); case .bool(let v): try c.encode(v); case .null: try c.encodeNil() }
    }
    subscript(_ key: String) -> JSON { if case .object(let o) = self { return o[key] ?? .null }; return .null }
    var string: String { if case .string(let s) = self { return s }; return "" }
    var array: [JSON] { if case .array(let a) = self { return a }; return [] }
    var object: [String: JSON] { if case .object(let o) = self { return o }; return [:] }
    var int: Int { if case .number(let n) = self { return Int(n) }; return 0 }
    var bool: Bool { if case .bool(let b) = self { return b }; return false }
    var pretty: String { let e = JSONEncoder(); e.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]; return (try? e.encode(self)).flatMap { String(data: $0, encoding: .utf8) } ?? "" }
}

struct MantleAgent: Identifiable, Equatable {
    let raw: JSON
    var id: String { raw["name"].string }
    var name: String { id }
    var title: String { name.replacingOccurrences(of: "-", with: " ").replacingOccurrences(of: "_", with: " ").capitalized }
    var description: String { raw["description"].string }
    var provider: String { raw["provider"].string }
    var model: String { raw["model"].string }
    var runtime: String { raw["runtime"].string }
    var config: JSON { raw["config"] }
    var yaml: String { raw["yaml"].string }
    var path: String { raw["path"].string }
    var skills: [JSON] { config["resources"]["skills"].array }
    var packages: [JSON] { config["resources"]["packages"].array }
    var servers: [String] { config["mcp"]["servers"].object.keys.sorted() }
    var sessionCount: Int { raw["sessionCount"].int }
    var isolated: Bool { raw["isolated"].bool }
    var symbol: String {
        if name.contains("research") { return "sparkle.magnifyingglass" }
        if name.contains("review") { return "checkmark.bubble" }
        if name.contains("front") || name.contains("design") { return "rectangle.3.group" }
        if name.contains("invest") { return "eye" }
        return "square.stack.3d.up"
    }
}

struct AppFailure: LocalizedError { let message: String; var errorDescription: String? { message } }

struct Engine {
    static var resources: URL {
        if let override = ProcessInfo.processInfo.environment["MANTLE_DESKTOP_RESOURCES"] { return URL(fileURLWithPath: override) }
        return Bundle.main.resourceURL ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    }
    static var python: URL { resources.appendingPathComponent("python/bin/python3") }
    static var bridge: URL { resources.appendingPathComponent("bridge/mantle_bridge.py") }
    static var environment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = resources.appendingPathComponent("engine").path + ":" + resources.appendingPathComponent("dependencies").path
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["MANTLE_QUIET"] = "1"
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        env["PATH"] = [resources.appendingPathComponent("node/bin").path, resources.appendingPathComponent("bin").path, home + "/.local/bin", "/opt/homebrew/bin", "/opt/homebrew/opt/node@22/bin", "/usr/local/bin", env["PATH"] ?? "/usr/bin:/bin"].joined(separator: ":")
        return env
    }
    static func request(_ operation: String, _ parameters: [String: JSON] = [:]) async throws -> JSON {
        var body = parameters; body["operation"] = .string(operation)
        let payload = try JSONEncoder().encode(JSON.object(body))
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    guard FileManager.default.isExecutableFile(atPath: python.path) else { throw AppFailure(message: "The bundled Mantle engine is missing. Rebuild the app using desktop/scripts/build_app.py.") }
                    let process = Process(); process.executableURL = python; process.arguments = [bridge.path]
                    process.environment = environment
                    let output = Pipe(), input = Pipe()
                    process.standardOutput = output; process.standardInput = input
                    process.standardError = FileHandle.nullDevice
                    try process.run()
                    try input.fileHandleForWriting.write(contentsOf: payload)
                    try input.fileHandleForWriting.close()
                    let data = output.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    guard !data.isEmpty else { throw AppFailure(message: "The Mantle engine exited without a response (status \(process.terminationStatus)).") }
                    let response = try JSONDecoder().decode(JSON.self, from: data)
                    guard response["ok"].bool else { throw AppFailure(message: response["error"].string.isEmpty ? "Mantle could not complete that action." : response["error"].string) }
                    continuation.resume(returning: response["result"])
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
    static func quote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
    @MainActor static func openTerminal(arguments: [String], directory: String? = nil) throws {
        let commandDirectory = FileManager.default.temporaryDirectory.appendingPathComponent("Mantle-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: commandDirectory, withIntermediateDirectories: true)
        let script = commandDirectory.appendingPathComponent("Mantle.command")
        let env = environment
        var contents = "#!/bin/zsh\n"
        for key in ["PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PATH", "MANTLE_HOME"] {
            if let value = env[key] { contents += "export \(key)=\(quote(value))\n" }
        }
        if let directory { contents += "cd -- \(quote(directory)) || exit 1\n" }
        contents += ([python.path, bridge.path, "--cli"] + arguments).map(quote).joined(separator: " ") + "\n"
        contents += "result=$?\nprintf '\\nMantle finished. You can close this window.\\n'\nexit $result\n"
        try contents.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
        NSWorkspace.shared.open(script)
    }
}

enum Destination: String, CaseIterable, Identifiable {
    case agents = "Agents", sessions = "Sessions", registry = "Registry", connections = "Connections", runtimes = "Runtimes", health = "Health"
    var id: Self { self }
    var symbol: String { switch self { case .agents: return "square.stack.3d.up"; case .sessions: return "clock"; case .registry: return "network"; case .connections: return "key.horizontal"; case .runtimes: return "shippingbox"; case .health: return "heart.text.square" } }
}

@MainActor final class AppModel: ObservableObject {
    @Published var snapshot: JSON = .null
    @Published var agents: [MantleAgent] = []
    @Published var selection: String?
    @Published var destination: Destination = .agents
    @Published var busy = false
    @Published var loading = true
    @Published var error: String?
    @Published var notice: String?
    @Published var showCreate = false
    @Published var editingAgent: MantleAgent?
    @Published var launchAgent: MantleAgent?
    @Published var search = ""
    @Published var connectionsTab = "Providers"
    @Published var registryToken = ""
    @Published var registryTokenURL = ""
    // A window whose launch isn't listed was restored by macOS and must not start an agent.
    @Published private(set) var openedLaunches: Set<String> = []
    private var firstMessages: [String: String] = [:]
    func prepareSession(_ launch: SessionLaunch, firstMessage: String = "") { openedLaunches.insert(launch.launchID); firstMessages[launch.launchID] = firstMessage }
    func takeFirstMessage(_ launchID: String) -> String { firstMessages.removeValue(forKey: launchID) ?? "" }
    func showMainWindow() { NSApp.windows.first { $0.isVisible && $0.canBecomeMain && $0.title != "Mantle Session" }?.makeKeyAndOrderFront(nil) }
    var selectedAgent: MantleAgent? { agents.first { $0.id == selection } }
    var cloudURL: String { snapshot["settings"]["cloud_url"].string }
    var credentials: [JSON] { snapshot["credentials"].array }
    var providers: [JSON] { snapshot["providers"].array }
    var home: String { snapshot["home"].string }
    var runtimes: [JSON] { snapshot["runtimes"].array }
    var filteredAgents: [MantleAgent] { agents.filter { search.isEmpty || ($0.name + $0.description + $0.model).localizedCaseInsensitiveContains(search) } }
    func refresh() async {
        do {
            let value = try await Engine.request("snapshot")
            snapshot = value; agents = value["agents"].array.map(MantleAgent.init)
            if selection == nil || !agents.contains(where: { $0.id == selection }) { selection = agents.first?.id }
        } catch { self.error = error.localizedDescription }
        loading = false
    }
    func request(_ operation: String, _ parameters: [String: JSON] = [:]) async throws -> JSON {
        var p = parameters
        if operation.hasPrefix("cloud.") && !registryToken.isEmpty {
            let explicitURL = parameters["url"]?.string ?? ""
            let target = explicitURL.isEmpty ? cloudURL : explicitURL
            if let tokenOrigin = Self.registryOrigin(registryTokenURL), Self.registryOrigin(target) == tokenOrigin {
                // An explicit target prevents MANTLE_CLOUD_URL from redirecting a
                // token-bearing request away from the registry shown in the app.
                p["url"] = .string(target)
                p["registryToken"] = .string(registryToken)
                p["registryTokenOrigin"] = .string(tokenOrigin)
            }
        }
        return try await Engine.request(operation, p)
    }
    static func registryOrigin(_ value: String) -> String? {
        guard let components = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = components.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = components.host?.lowercased(), !host.isEmpty else { return nil }
        var origin = URLComponents()
        origin.scheme = scheme
        origin.host = host
        origin.port = components.port ?? (scheme == "https" ? 443 : 80)
        return origin.string
    }
    @discardableResult func perform(_ operation: String, _ parameters: [String: JSON] = [:], success: String? = nil) async -> JSON? {
        guard !busy else { return nil }; busy = true; defer { busy = false }
        do { let result = try await request(operation, parameters); await refresh(); notice = success; return result }
        catch { self.error = error.localizedDescription; return nil }
    }
    func terminal(_ arguments: [String], directory: String? = nil) {
        do { try Engine.openTerminal(arguments: arguments, directory: directory); notice = "Opened in Terminal. Refresh Mantle when you’re finished." }
        catch { self.error = error.localizedDescription }
    }
}

extension Color {
    init(hex: UInt32) { self.init(red: Double((hex >> 16) & 255) / 255, green: Double((hex >> 8) & 255) / 255, blue: Double(hex & 255) / 255) }
    static let mantleAccent = Color(hex: 0x7AA2F7)
    static let mantleCanvas = Color(hex: 0x161618)
    static let mantleCard = Color(hex: 0x1E1E22)
    static let mantleInk = Color(hex: 0xE6E4E0)
}

struct MantleMark: View {
    var size: CGFloat = 40
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.25).fill(Color(hex: 0x111113))
            Path { p in
                p.move(to: CGPoint(x: 6, y: 25.2)); p.addCurve(to: CGPoint(x: 10.4,y: 12.8), control1: CGPoint(x: 7.8,y: 20), control2: CGPoint(x: 9.2,y: 15.6)); p.addLine(to: CGPoint(x: 16,y: 19.4)); p.addLine(to: CGPoint(x: 21.6,y: 12.8)); p.addCurve(to: CGPoint(x: 26,y: 25.2),control1: CGPoint(x: 22.8,y: 15.6), control2: CGPoint(x: 24.2,y: 20))
            }.strokedPath(StrokeStyle(lineWidth: 3.4,lineCap: .round,lineJoin: .round)).fill(Color.mantleInk).scaleEffect(size / 32, anchor: .topLeading)
            Path { p in p.move(to: CGPoint(x: 13.2,y: 13.2)); p.addCurve(to: CGPoint(x: 16,y: 6),control1: CGPoint(x: 12.9,y: 9.3),control2: CGPoint(x: 14.2,y: 6.8)); p.addCurve(to: CGPoint(x: 18.8,y: 13.2),control1: CGPoint(x: 17.8,y: 6.8),control2: CGPoint(x: 19.1,y: 9.3)) }.strokedPath(StrokeStyle(lineWidth: 2.2,lineCap: .round,lineJoin: .round)).fill(Color.mantleInk).scaleEffect(size / 32, anchor: .topLeading)
        }.frame(width: size,height: size).accessibilityLabel("Mantle")
    }
}

struct PageHeader: View {
    let title: String; let subtitle: String; var symbol: String = ""
    var body: some View { HStack(alignment: .top, spacing: 16) { if !symbol.isEmpty { Image(systemName: symbol).font(.system(size: 26,weight: .medium)).foregroundStyle(Color.mantleAccent).frame(width: 52,height: 52).background(Color.mantleAccent.opacity(0.10),in: RoundedRectangle(cornerRadius: 14)) }; VStack(alignment: .leading,spacing: 6) { Text(title).font(.system(size: 27,weight: .bold,design: .rounded)); Text(subtitle).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false,vertical: true) }; Spacer() } }
}
struct Surface<Content: View>: View {
    @Environment(\.colorScheme) private var colorScheme
    var title: String? = nil; @ViewBuilder var content: Content
    var body: some View { VStack(alignment: .leading,spacing: 16) { if let title { Text(title).font(.headline) }; content }.padding(20).frame(maxWidth: .infinity,alignment: .leading).background(colorScheme == .dark ? Color.mantleCard : Color(nsColor: .controlBackgroundColor),in: RoundedRectangle(cornerRadius: 14)).overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(.primary.opacity(0.07))) }
}
struct EmptyState: View {
    let symbol: String; let title: String; let detail: String
    var body: some View { VStack(spacing: 14) { Image(systemName: symbol).font(.system(size: 38,weight: .light)).foregroundStyle(Color.mantleAccent); Text(title).font(.title2.weight(.semibold)); Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 390) }.frame(maxWidth: .infinity,maxHeight: .infinity).padding(36) }
}
struct StatusPill: View {
    let label: String; var color: Color = .mantleAccent
    var body: some View { Text(label).font(.caption.weight(.medium)).foregroundStyle(color).padding(.horizontal,9).padding(.vertical,4).background(color.opacity(0.10),in: Capsule()) }
}

// macOS saves this value with the window, so it must never hold anything that acts when restored.
// The first message stays in AppModel for this app run only.
struct SessionLaunch: Codable, Hashable {
    var launchID: String
    var agentName: String
    var directory: String
    var includeContext: Bool
    var projectRoot: String
    var sessionID: String
    init(agentName: String, directory: String, includeContext: Bool = true, projectRoot: String = "", sessionID: String = "") {
        self.launchID = sessionID.isEmpty ? UUID().uuidString : "resume:\(agentName):\(sessionID)"
        self.agentName = agentName; self.directory = directory
        self.includeContext = includeContext; self.projectRoot = projectRoot; self.sessionID = sessionID
    }
    static func == (lhs: Self, rhs: Self) -> Bool { lhs.launchID == rhs.launchID }
    func hash(into hasher: inout Hasher) { hasher.combine(launchID) }
}
