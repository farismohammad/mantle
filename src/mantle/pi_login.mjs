// Run a Pi provider sign-in directly from the terminal, without opening Pi's TUI.
//
// Usage: node pi_login.mjs PI_PACKAGE_DIR PROVIDER_ID AUTH_JSON
//
// Mantle does not implement OAuth: this calls the same ModelRuntime.login that
// Pi's /login uses, so tokens are written in Pi's format, under Pi's file lock.
// Exit codes: 0 signed in, 1 failed, 3 this Pi version lacks the API (the
// caller falls back to Pi's own /login), 130 cancelled.
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline/promises";
import { pathToFileURL } from "node:url";

const INCOMPATIBLE = 3;
const [packageDir, providerId, authPath] = process.argv.slice(2);

function openBrowser(url) {
    const [command, args] = process.platform === "darwin" ? ["open", [url]] : ["xdg-open", [url]];
    spawn(command, args, { stdio: "ignore", detached: true }).on("error", () => {}).unref();
}

async function loadPi() {
    try {
        const pi = await import(pathToFileURL(join(packageDir, "dist", "index.js")).href);
        if (typeof pi.ModelRuntime?.create !== "function") return undefined;
        return pi;
    } catch {
        return undefined;
    }
}

function deviceId(pi, agentDir) {
    // Reuse Pi's own installation ID so providers see one device, as with /login.
    const cwd = mkdtempSync(join(tmpdir(), "mantle-login-"));
    try {
        return pi.SettingsManager?.create(cwd, agentDir).getOrCreateDeviceId() ?? randomUUID();
    } catch {
        return randomUUID();
    } finally {
        rmSync(cwd, { recursive: true, force: true });
    }
}

const pi = await loadPi();
if (!pi) process.exit(INCOMPATIBLE);

const controller = new AbortController();
const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: process.stdin.isTTY });
let muted = false;
const write = rl._writeToOutput?.bind(rl);
if (write) rl._writeToOutput = (text) => { if (!muted || text.includes("\n")) write(muted ? "\n" : text); };

// Queue lines so input that arrives before the next question (a paste, or piped
// answers) is not lost between prompts.
const lines = [];
let waiting;
rl.on("line", (line) => (waiting ? waiting(line) : lines.push(line)));
rl.on("SIGINT", () => controller.abort());
// Standard input closed (Ctrl+D): nothing more can be answered.
rl.on("close", () => controller.abort());
process.on("SIGINT", () => controller.abort());

function nextLine(signal) {
    if (lines.length) return Promise.resolve(lines.shift());
    return new Promise((resolve, reject) => {
        if (signal.aborted) return reject(new Error("Login cancelled"));
        const onAbort = () => { waiting = undefined; reject(new Error("Login cancelled")); };
        signal.addEventListener("abort", onAbort, { once: true });
        waiting = (line) => { waiting = undefined; signal.removeEventListener("abort", onAbort); resolve(line); };
    });
}

async function ask(question, signal, secret = false) {
    rl.setPrompt(question);
    rl.prompt();
    muted = secret;
    try {
        return (await nextLine(signal ? AbortSignal.any([controller.signal, signal]) : controller.signal)).trim();
    } catch (error) {
        // Without a cancel, the provider withdrew the question, e.g. the browser callback arrived.
        if (!controller.signal.aborted) process.stdout.write("\n");
        throw error;
    } finally {
        muted = false;
    }
}

async function prompt(request) {
    if (request.type === "select") {
        console.log(`\n${request.message}`);
        request.options.forEach((option, index) => console.log(`  ${index + 1}. ${option.label}`));
        const answer = await ask(`Choose 1-${request.options.length} [1]: `, request.signal);
        const selected = request.options[answer === "" ? 0 : Number.parseInt(answer, 10) - 1];
        if (!selected) throw new Error("Invalid selection");
        return selected.id;
    }
    const hint = request.placeholder ? ` (e.g. ${request.placeholder})` : "";
    return ask(`\n${request.message}${hint}\n> `, request.signal, request.type === "secret");
}

function notify(event) {
    if (event.type === "auth_url") {
        console.log(`\nOpening your browser to sign in. If it does not open, visit:\n  ${event.url}`);
        if (event.instructions) console.log(event.instructions);
        openBrowser(event.url);
    } else if (event.type === "device_code") {
        console.log(`\nOpen ${event.verificationUri} and enter the code: ${event.userCode}`);
        openBrowser(event.verificationUri);
        console.log("Waiting for you to finish in the browser…");
    } else if (event.type === "info") {
        console.log(event.message);
        for (const link of event.links ?? []) console.log(`  ${link.label ? `${link.label}: ` : ""}${link.url}`);
    } else {
        console.log(event.message);
    }
}

let status = 0;
try {
    const agentDir = join(authPath, "..");
    const runtime = await pi.ModelRuntime.create({ authPath, modelsPath: null, refreshOnCreate: false });
    if (!runtime.getProvider(providerId)?.auth?.oauth) {
        console.error(`Pi has no browser login for "${providerId}".`);
        status = 1;
    } else {
        await runtime.login(providerId, "oauth", { signal: controller.signal, prompt, notify },
            { getDeviceId: () => deviceId(pi, agentDir) });
    }
} catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (controller.signal.aborted || message === "Login cancelled") {
        console.error("\nSign-in cancelled.");
        status = 130;
    } else if (error?.name === "CredentialSynchronizationError") {
        // The credential was stored; only Pi's local model snapshot lagged behind.
        status = 0;
    } else {
        console.error(`\nSign-in failed: ${message}`);
        status = 1;
    }
} finally {
    rl.close();
}
process.exit(status);
