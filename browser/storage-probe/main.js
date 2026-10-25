const DATABASE_NAME = "workspace-storage-prototype-wipe-me.db";
const started = performance.now();
const status = document.querySelector("#status");
const evidence = document.querySelector("#evidence");
const note = document.querySelector("#note");

function hex(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function openProbe() {
  if (!crossOriginIsolated && isSecureContext && navigator.serviceWorker) {
    status.textContent = "Preparing browser isolation; this page will reload once…";
    // coi-serviceworker reloads under isolated response headers after registration.
    await new Promise((resolve, reject) => setTimeout(() => reject(new Error("Browser isolation did not become available after service-worker registration.")), 15_000));
  }
  if (!crossOriginIsolated || !navigator.storage?.getDirectory) {
    throw new Error("This experiment needs cross-origin isolation and OPFS browser storage.");
  }
  const { connect } = await import("@tursodatabase/database-wasm");
  const db = await connect(DATABASE_NAME);
  await db.exec(`
    CREATE TABLE IF NOT EXISTS device_identity (
      slot INTEGER PRIMARY KEY CHECK (slot = 1),
      device_id TEXT NOT NULL,
      private_key BLOB NOT NULL
    ) STRICT;
    CREATE TABLE IF NOT EXISTS workspaces (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL
    ) STRICT;
    CREATE TABLE IF NOT EXISTS notes (
      id TEXT PRIMARY KEY,
      workspace_id TEXT NOT NULL,
      body TEXT NOT NULL,
      revision INTEGER NOT NULL
    ) STRICT;
  `);
  let identity = await db.prepare("SELECT device_id, private_key FROM device_identity WHERE slot = 1").get();
  const created = identity === undefined;
  if (created) {
    const pair = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
    const publicKey = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
    const privateKey = new Uint8Array(await crypto.subtle.exportKey("pkcs8", pair.privateKey));
    await db.exec("BEGIN");
    try {
      await db.prepare("INSERT INTO device_identity VALUES (1, ?, ?)").run(hex(publicKey), privateKey);
      await db.prepare("INSERT INTO workspaces VALUES (?, ?)").run("launch", "Launch workspace");
      await db.prepare("INSERT INTO notes VALUES (?, ?, ?, ?)").run("first-note", "launch", "This note belongs to this browser host.", 1);
      await db.exec("COMMIT");
    } catch (error) {
      await db.exec("ROLLBACK");
      throw error;
    }
    identity = await db.prepare("SELECT device_id, private_key FROM device_identity WHERE slot = 1").get();
  }
  const publicBytes = Uint8Array.from(identity.device_id.match(/../g), (value) => Number.parseInt(value, 16));
  const privateKey = await crypto.subtle.importKey("pkcs8", identity.private_key, "Ed25519", false, ["sign"]);
  const publicKey = await crypto.subtle.importKey("raw", publicBytes, "Ed25519", false, ["verify"]);
  const challenge = crypto.getRandomValues(new Uint8Array(32));
  const signature = await crypto.subtle.sign("Ed25519", privateKey, challenge);
  const identityVerified = await crypto.subtle.verify("Ed25519", publicKey, signature, challenge);
  if (!identityVerified) throw new Error("The recovered device key failed its signing check.");
  const startupMs = Math.round(performance.now() - started);

  async function read() {
    const workspace = await db.prepare("SELECT * FROM workspaces WHERE id = ?").get("launch");
    const savedNote = await db.prepare("SELECT * FROM notes WHERE id = ?").get("first-note");
    const files = [];
    const directory = await navigator.storage.getDirectory();
    for await (const [name, handle] of directory.entries()) {
      if (name.startsWith(DATABASE_NAME) && handle.kind === "file") {
        files.push({ name, bytes: (await handle.getFile()).size });
      }
    }
    files.sort((left, right) => left.name.localeCompare(right.name));
    return {
      engine: "@tursodatabase/database-wasm@0.8.3",
      provider: "OPFS worker SyncAccessHandle (database + WAL)",
      device_id: identity.device_id,
      created_identity: created,
      identity_verified: identityVerified,
      workspace,
      note: savedNote,
      files,
      cross_origin_isolated: crossOriginIsolated,
      isolation_service_worker: navigator.serviceWorker.controller?.scriptURL ?? null,
      persistent_storage_granted: await navigator.storage.persisted(),
      startup_ms: startupMs,
    };
  }

  async function save(body) {
    if (typeof body !== "string" || new TextEncoder().encode(body).length > 128 * 1024) {
      throw new Error("A note must be text with at most 128 KiB.");
    }
    await db.prepare("UPDATE notes SET body = ?, revision = revision + 1 WHERE id = ?").run(body, "first-note");
    return read();
  }

  async function verifyStrict() {
    const before = await db.prepare("SELECT revision FROM notes WHERE id = ?").get("first-note");
    let rejection = null;
    try {
      await db.prepare("UPDATE notes SET revision = ? WHERE id = ?").run("not an integer", "first-note");
    } catch (error) {
      rejection = String(error);
    }
    const after = await db.prepare("SELECT revision FROM notes WHERE id = ?").get("first-note");
    if (rejection === null || before.revision !== after.revision) {
      throw new Error("STRICT integer validation did not preserve the saved revision.");
    }
    return { rejected: true, message: rejection, retained_revision: after.revision };
  }

  return { read, save, verifyStrict };
}

function show(value) {
  note.value = value.note.body;
  evidence.textContent = JSON.stringify(value, null, 2);
  return value;
}

document.querySelector("#reload").onclick = () => location.reload();

try {
  const probe = await openProbe();
  window.storageProbe = probe;
  show(await probe.read());
  status.textContent = "Saved locally. Reload or close and reopen this page to recover it.";
  document.querySelector("#save").disabled = false;
  document.querySelector("#persist").disabled = false;
  note.disabled = false;
  document.querySelector("#save").onclick = async () => {
    try {
      show(await probe.save(note.value));
      status.textContent = "Note saved in the browser database.";
    } catch (error) {
      status.textContent = String(error);
    }
  };
  document.querySelector("#persist").onclick = async () => {
    const granted = await navigator.storage.persist();
    show(await probe.read());
    status.textContent = granted ? "Persistent storage granted." : "The browser did not grant persistent storage; local files can still be evicted.";
  };
} catch (error) {
  window.storageProbeError = String(error);
  status.textContent = String(error);
}
