// Builds a fixture data root in a temp folder through relay.py commands,
// starts `relay.py serve` on it on a free port, and stops the server and
// removes the folder after the tests. Nothing here lands in the repo: the
// tokens that `onboard` prints and the PIN stay in this process.
import { execFileSync, spawn } from "node:child_process";
import { randomInt } from "node:crypto";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer, type AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("../..", import.meta.url));
const RELAY = join(REPO, "scripts", "relay.py");

/** The ids of the fixture's messages, which the tests open by their titles. */
export const FIXTURE = {
  openQuestion: "Ship the beta build today?",
  answerQuestion: "Which port should the test server use?",
  thread: "Schema change ready",
  linked: "Cache warmed",
  ownerRequest: "Read the new runbook",
  /** Only markfeedback.spec.ts reads and replies to this thread. */
  feedback: "Index rebuilt",
};

function freePort(): Promise<number> {
  return new Promise((ok, fail) => {
    const s = createServer();
    s.on("error", fail);
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address() as AddressInfo;
      s.close(() => ok(port));
    });
  });
}

/**
 * The environment of every relay.py run: HOME and each state file the server
 * or a command may write sit in the temp folder, so a run never touches the
 * user's home or a real install.
 */
function envFor(root: string): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const [k, v] of Object.entries(process.env)) if (!k.startsWith("CROSSTALK_")) env[k] = v;
  const at = (name: string) => join(root, ".home", name);
  return {
    ...env,
    HOME: join(root, ".home"),
    CROSSTALK_DATA: root,
    CROSSTALK_SESSIONS: at(".crosstalk-sessions.json"),
    CROSSTALK_NOTIFY_STATE: at(".crosstalk-notify-state.json"),
    CROSSTALK_LIVE_DIR: at(".crosstalk-live"),
    CROSSTALK_URGENT_STATE: at(".crosstalk-urgent-state.json"),
    CROSSTALK_REVIEWS_STATE: at(".crosstalk-reviews-state.json"),
    CROSSTALK_BUILDHOST_STATE: at(".crosstalk-buildhost-state.json"),
  };
}

export function relay(root: string, ...args: string[]): string {
  // stdout is kept from the console: `onboard` prints a token. An error names
  // the command and its stderr, not its arguments: `set-pin` takes the PIN.
  try {
    return execFileSync("python3", [RELAY, "--data-root", root, ...args], { cwd: REPO, env: envFor(root), encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  } catch (e) {
    throw new Error(`relay.py ${args[0]} failed: ${String((e as { stderr?: string }).stderr ?? "").trim()}`);
  }
}

/** The time the log gives message `id`. */
function timeOf(root: string, id: string): string {
  for (const name of readdirSync(join(root, "log"))) {
    for (const line of readFileSync(join(root, "log", name), "utf8").split("\n")) {
      if (!line.trim()) continue;
      const row = JSON.parse(line) as { id?: string; t?: string };
      if (row.id === id && row.t) return row.t;
    }
  }
  throw new Error("no message " + id + " in the fixture log");
}

function buildFixture(root: string): string {
  relay(root, "onboard", "alpha", "--name", "Alpha", "--kind", "project");
  relay(root, "onboard", "relay-bot", "--name", "Relay bot", "--kind", "tool");
  relay(root, "onboard", "beta", "--name", "Beta", "--kind", "agent", "--parent", "alpha");
  // The owner approves each participant on the page; the fixture does it in the roster.
  const rosterFile = join(root, "participants.json");
  const roster = JSON.parse(readFileSync(rosterFile, "utf8")) as { participants: { trust?: string }[] };
  for (const p of roster.participants) p.trust = "approved";
  writeFileSync(rosterFile, JSON.stringify(roster, null, 2) + "\n");

  const pin = String(randomInt(100000, 1000000));
  relay(root, "set-pin", pin);

  const who = ["--harness", "codex", "--model", "gpt-5"];
  relay(root, "append", ...who, "--from", "beta", "--op", "set", "--collection", "messages", "--id", "beta-e2e-schema",
    "--data", JSON.stringify({ from: "beta", kind: "sync", summary: FIXTURE.thread, body: "The new column is in the branch.", to: ["alpha"] }));
  relay(root, "ask", ...who, "--from", "beta", "--id", "beta-e2e-port", "--summary", FIXTURE.answerQuestion,
    "--body", "The tests need one port.", "--option", "A free port", "--option", "A fixed port", "--recommend", "1");
  relay(root, "ask", ...who, "--from", "alpha", "--id", "alpha-e2e-ship", "--summary", FIXTURE.openQuestion,
    "--body", "The tests pass.", "--option", "Ship it", "--option", "Wait a day", "--recommend", "1");
  // The replies come a second later than the root, so they sort after the owner's mark.
  execFileSync("sleep", ["1.1"]);
  // A request that names the owner: Needs you lists it after the two questions, which are a second older.
  relay(root, "append", ...who, "--from", "alpha", "--op", "set", "--collection", "messages", "--id", "alpha-e2e-runbook",
    "--data", JSON.stringify({ from: "alpha", kind: "request", summary: FIXTURE.ownerRequest, body: "It is in docs/.", to: ["owner"] }));
  relay(root, "reply", ...who, "--from", "alpha", "--to-id", "beta-e2e-schema", "--summary", "Looks good", "--body", "I read the migration.");
  relay(root, "reply", ...who, "--from", "relay-bot", "--to-id", "beta-e2e-schema", "--summary", "Merged", "--body", "The branch is merged.");
  // A thread with a reply, so the unanswered order leaves it out: the stream shows it as a thread opened by its id.
  relay(root, "append", ...who, "--from", "beta", "--op", "set", "--collection", "messages", "--id", "beta-e2e-cache",
    "--data", JSON.stringify({ from: "beta", kind: "sync", summary: FIXTURE.linked, body: "The cache is warm.", to: ["alpha"] }));
  relay(root, "reply", ...who, "--from", "alpha", "--to-id", "beta-e2e-cache", "--summary", "Thanks", "--body", "I saw it.");
  // A thread of two participants (one participant's thread folds as routine) for markfeedback.spec.ts alone.
  relay(root, "append", ...who, "--from", "beta", "--op", "set", "--collection", "messages", "--id", "beta-e2e-index",
    "--data", JSON.stringify({ from: "beta", kind: "sync", summary: FIXTURE.feedback, body: "The index is rebuilt.", to: ["alpha"] }));
  relay(root, "reply", ...who, "--from", "alpha", "--to-id", "beta-e2e-index", "--summary", "Noted", "--body", "I will use it.");
  // The owner read the thread's root before the replies came.
  writeFileSync(join(root, "read-state.json"),
    JSON.stringify({ threads: { "beta-e2e-schema": { t: timeOf(root, "beta-e2e-schema"), id: "beta-e2e-schema" } }, muted: [], all_before: null }));
  return pin;
}

export default async function globalSetup() {
  const root = mkdtempSync(join(tmpdir(), "crosstalk-e2e-"));
  mkdirSync(join(root, ".home"));
  let pin: string;
  let port: number;
  try {
    pin = buildFixture(root);
    port = await freePort();
  } catch (e) {
    rmSync(root, { recursive: true, force: true });
    throw e;
  }
  const server = spawn("python3", [RELAY, "--data-root", root, "serve", "--port", String(port), "--no-notify", "--no-autogrant"], {
    cwd: REPO,
    env: envFor(root),
    stdio: ["ignore", "pipe", "pipe"],
  });
  // The server prints its startup PIN; its output never reaches the console.
  let err = "";
  server.stderr.on("data", (b: Buffer) => (err += b.toString()));
  const stop = async () => {
    if (server.exitCode === null && server.signalCode === null) {
      const stopped = new Promise((ok) => server.once("exit", ok));
      server.kill("SIGTERM");
      await stopped;
    }
    rmSync(root, { recursive: true, force: true });
  };
  await new Promise<void>((ok, fail) => {
    let out = "";
    const timer = setTimeout(() => fail(new Error("relay.py serve did not start in 20 s: " + err)), 20000);
    server.stdout.on("data", (b: Buffer) => {
      out += b.toString();
      if (out.includes("Crosstalk is serving")) {
        clearTimeout(timer);
        ok();
      }
    });
    server.on("exit", (code) => {
      clearTimeout(timer);
      fail(new Error(`relay.py serve stopped with code ${code}: ${err}`));
    });
  }).catch(async (e: unknown) => {
    await stop();
    throw e;
  });
  process.env.CROSSTALK_E2E_URL = `http://127.0.0.1:${port}`;
  process.env.CROSSTALK_E2E_PIN = pin;
  // The data root, for a test that sets the read state and puts it back.
  process.env.CROSSTALK_E2E_ROOT = root;
  return stop;
}
