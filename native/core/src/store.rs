//! One serialized owner for the actual Turso embedded database.
//! All writes validate the full resulting signed history before a transaction commits.
use crate::{
    identity,
    model::{
        self, Action, Command, CommandReceipt, ImportReceipt, Operation, PeerRecord,
        SignedOperation, Snapshot,
    },
};
use anyhow::{Context, Result, bail, ensure};
use iroh::{EndpointAddr, SecretKey};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    path::Path,
    sync::Arc,
};
use tokio::sync::Mutex;
use turso::{Connection, Database};

#[derive(Clone)]
pub struct Store {
    inner: Arc<Inner>,
}
struct Inner {
    key: SecretKey,
    name: String,
    state: Mutex<State>,
    _owner: File,
}
struct State {
    conn: Connection,
    _database: Database,
    operations: Vec<SignedOperation>,
    projection: model::Projection,
    requests: BTreeMap<String, Command>,
    peers: BTreeMap<String, PeerRecord>,
    fingerprints: BTreeMap<String, String>,
}
impl State {
    fn install(
        &mut self,
        operations: Vec<SignedOperation>,
        projection: model::Projection,
    ) -> Result<()> {
        self.fingerprints = workspace_fingerprints(&operations)?;
        self.operations = operations;
        self.projection = projection;
        Ok(())
    }
}

impl Store {
    pub async fn open(directory: impl AsRef<Path>, name: &str) -> Result<Self> {
        model::validate_device_name(name)?;
        let directory = directory.as_ref();
        let (key, owner) = identity::open(directory)?;
        let database_path = directory.join("workspaces.db");
        let database =
            turso::Builder::new_local(database_path.to_str().context("data path is not UTF-8")?)
                .build()
                .await?;
        let conn = database.connect()?;
        conn.execute_batch("PRAGMA synchronous = FULL;
            CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL) STRICT;
            CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
            CREATE TABLE IF NOT EXISTS peers (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
            CREATE TABLE IF NOT EXISTS invitations (token_hash TEXT PRIMARY KEY, workspace_ids TEXT NOT NULL, expires_at_ms INTEGER NOT NULL, accepted_peer TEXT) STRICT;").await?;
        // Validate retained external data once before publishing a read model.
        let operations = load_operations(&conn).await?;
        let projection = model::project(&operations)?;
        let fingerprints = workspace_fingerprints(&operations)?;
        let peers = load_peers(&conn).await?;
        let requests = load_requests(&conn, &operations, &key.public().to_string()).await?;
        Ok(Self {
            inner: Arc::new(Inner {
                key,
                name: name.to_string(),
                state: Mutex::new(State {
                    conn,
                    _database: database,
                    operations,
                    projection,
                    requests,
                    peers,
                    fingerprints,
                }),
                _owner: owner,
            }),
        })
    }
    pub fn secret_key(&self) -> SecretKey {
        self.inner.key.clone()
    }
    pub fn device_id(&self) -> String {
        self.inner.key.public().to_string()
    }
    pub async fn snapshot(&self) -> Result<Snapshot> {
        let state = self.inner.state.lock().await;
        Ok(Snapshot {
            device_id: self.device_id(),
            device_name: self.inner.name.clone(),
            workspaces: state.projection.workspaces.values().cloned().collect(),
            resources: state.projection.resources.values().cloned().collect(),
            operation_count: state.operations.len(),
        })
    }
    pub async fn command(&self, command: Command) -> Result<CommandReceipt> {
        let owner = self.clone();
        tokio::spawn(async move { owner.command_owned(command).await })
            .await
            .context("workspace command owner ended")?
    }
    async fn command_owned(&self, command: Command) -> Result<CommandReceipt> {
        let mut state = self.inner.state.lock().await;
        let (id, workspace_id) = command.identity();
        let prepared = match model::prepare_command(
            &command,
            &self.inner.key,
            &state.operations,
            &state.projection,
            &state.requests,
        ) {
            Ok(prepared) => prepared,
            Err(error) => {
                return Ok(CommandReceipt::Rejected {
                    operation_id: id.into(),
                    workspace_id: workspace_id.into(),
                    message: error.to_string(),
                });
            }
        };
        let write = match prepared {
            model::PreparedCommand::Reply(reply) => return Ok(reply),
            model::PreparedCommand::Write(write) => write,
        };
        // From this point onward an error is uncertain, never a domain rejection.
        let tx = state.conn.transaction().await?;
        insert_operation(&tx, &write.signed).await?;
        tx.execute(
            "INSERT INTO requests(id,payload) VALUES(?1,?2)",
            (id, write.request_payload.as_str()),
        )
        .await?;
        tx.commit().await?;
        state.install(write.operations, write.projection)?;
        state.requests.insert(id.to_string(), command.clone());
        Ok(CommandReceipt::Applied {
            operation_id: id.into(),
            workspace_id: workspace_id.into(),
            replayed: false,
        })
    }
    pub async fn operations(&self, workspace_ids: &[String]) -> Result<Vec<SignedOperation>> {
        validate_workspaces(workspace_ids)?;
        let state = self.inner.state.lock().await;
        for id in workspace_ids {
            ensure!(
                state.projection.workspaces.contains_key(id),
                "selected workspace is absent"
            );
        }
        Ok(state
            .operations
            .iter()
            .filter(|s| workspace_ids.contains(&s.operation.workspace_id))
            .cloned()
            .collect())
    }
    pub async fn allowed_workspaces(&self, peer_id: &str) -> Result<Vec<String>> {
        model::validate_device(peer_id)?;
        let state = self.inner.state.lock().await;
        Ok(state
            .projection
            .workspaces
            .values()
            .filter(|w| w.members.iter().any(|id| id == peer_id))
            .map(|w| w.id.clone())
            .collect())
    }
    pub async fn fingerprint(&self, workspace_ids: &[String]) -> Result<String> {
        validate_workspaces(workspace_ids)?;
        let state = self.inner.state.lock().await;
        let mut ids = workspace_ids.to_vec();
        ids.sort();
        let selected: Vec<_> = ids
            .iter()
            .map(|id| {
                state
                    .fingerprints
                    .get(id)
                    .map(|digest| (id, digest))
                    .context("selected workspace is absent")
            })
            .collect::<Result<_>>()?;
        Ok(hex::encode(Sha256::digest(serde_json::to_vec(&selected)?)))
    }
    pub async fn import(
        &self,
        authenticated_peer: &str,
        incoming: &[SignedOperation],
    ) -> Result<ImportReceipt> {
        let owner = self.clone();
        let peer = authenticated_peer.to_string();
        let incoming = incoming.to_vec();
        tokio::spawn(async move { owner.import_owned(&peer, &incoming).await })
            .await
            .context("workspace import owner ended")?
    }
    async fn import_owned(
        &self,
        authenticated_peer: &str,
        incoming: &[SignedOperation],
    ) -> Result<ImportReceipt> {
        model::validate_device(authenticated_peer)?;
        let mut state = self.inner.state.lock().await;
        let existing = state.operations.clone();
        let projection = &state.projection;
        for signed in incoming {
            let workspace = projection
                .workspaces
                .get(&signed.operation.workspace_id)
                .context("incoming workspace is not enrolled")?;
            ensure!(
                workspace.members.contains(&self.device_id()),
                "this device is not a workspace member"
            );
        }
        let (all, added, duplicates) = merge_operations(existing, incoming)?;
        let merged = if added.is_empty() {
            state.projection.clone()
        } else {
            model::project(&all)?
        };
        // A new member may carry an existing member's signed grant. Validation above
        // anchors that grant to our retained origin; no live inviter is required.
        for signed in incoming {
            let workspace = &merged.workspaces[&signed.operation.workspace_id];
            ensure!(
                workspace.members.iter().any(|id| id == authenticated_peer),
                "authenticated peer is not a workspace member"
            );
        }
        if !added.is_empty() {
            let tx = state.conn.transaction().await?;
            for signed in &added {
                insert_operation(&tx, signed).await?;
            }
            tx.commit().await?;
            state.install(all, merged)?;
        }
        Ok(ImportReceipt {
            imported: added.len(),
            already_present: duplicates,
        })
    }
    /// Called only after the host verifies the explicitly supplied invitation and pinned peer.
    pub async fn bootstrap(
        &self,
        peer: PeerRecord,
        workspace_ids: Vec<String>,
        incoming: Vec<SignedOperation>,
    ) -> Result<ImportReceipt> {
        let owner = self.clone();
        tokio::spawn(async move { owner.bootstrap_owned(peer, workspace_ids, incoming).await })
            .await
            .context("workspace enrollment owner ended")?
    }
    async fn bootstrap_owned(
        &self,
        peer: PeerRecord,
        workspace_ids: Vec<String>,
        incoming: Vec<SignedOperation>,
    ) -> Result<ImportReceipt> {
        validate_peer(&peer)?;
        validate_workspaces(&workspace_ids)?;
        ensure!(
            !workspace_ids.is_empty(),
            "invitation contains no workspaces"
        );
        ensure!(
            incoming
                .iter()
                .all(|s| workspace_ids.contains(&s.operation.workspace_id)),
            "bootstrap contains an unselected workspace"
        );
        let mut state = self.inner.state.lock().await;
        let existing = state.operations.clone();
        let (all, added, duplicates) = merge_operations(existing, &incoming)?;
        let projection = model::project(&all)?;
        for workspace_id in &workspace_ids {
            let workspace = projection
                .workspaces
                .get(workspace_id)
                .context("bootstrap omits a selected workspace")?;
            ensure!(
                workspace.members.contains(&peer.id),
                "inviter is not a workspace member"
            );
            ensure!(
                workspace.members.contains(&self.device_id()),
                "invitation does not grant this device membership"
            );
        }
        let tx = state.conn.transaction().await?;
        for signed in &added {
            insert_operation(&tx, signed).await?;
        }
        write_peer(&tx, &peer).await?;
        tx.commit().await?;
        state.install(all, projection)?;
        state.peers.insert(peer.id.clone(), peer);
        Ok(ImportReceipt {
            imported: added.len(),
            already_present: duplicates,
        })
    }
    pub async fn create_invite(
        &self,
        token_hash: String,
        workspace_ids: Vec<String>,
        expires_at_ms: i64,
    ) -> Result<()> {
        validate_token_hash(&token_hash)?;
        validate_workspaces(&workspace_ids)?;
        ensure!(
            !workspace_ids.is_empty() && expires_at_ms > 0,
            "invalid invitation scope or expiry"
        );
        let state = self.inner.state.lock().await;
        let projection = &state.projection;
        for id in &workspace_ids {
            let workspace = projection
                .workspaces
                .get(id)
                .context("invitation workspace is absent")?;
            ensure!(
                workspace.members.contains(&self.device_id()),
                "this device cannot share that workspace"
            );
        }
        state.conn.execute("INSERT INTO invitations(token_hash,workspace_ids,expires_at_ms,accepted_peer) VALUES(?1,?2,?3,NULL)", (token_hash, serde_json::to_string(&workspace_ids)?, expires_at_ms)).await?;
        Ok(())
    }
    pub async fn accept_invite(
        &self,
        token_hash: &str,
        peer: PeerRecord,
        now_ms: i64,
    ) -> Result<Vec<String>> {
        let owner = self.clone();
        let token_hash = token_hash.to_string();
        tokio::spawn(async move { owner.accept_invite_owned(&token_hash, peer, now_ms).await })
            .await
            .context("invitation acceptance owner ended")?
    }
    async fn accept_invite_owned(
        &self,
        token_hash: &str,
        peer: PeerRecord,
        now_ms: i64,
    ) -> Result<Vec<String>> {
        validate_token_hash(token_hash)?;
        validate_peer(&peer)?;
        ensure!(
            peer.id != self.device_id(),
            "cannot enroll this device as another peer"
        );
        let mut state = self.inner.state.lock().await;
        let invitation = {
            let mut rows = state.conn.query("SELECT workspace_ids, expires_at_ms, accepted_peer FROM invitations WHERE token_hash = ?1", [token_hash]).await?;
            let row = rows.next().await?.context("invitation is unknown")?;
            let ids: Vec<String> = serde_json::from_str(&row.get::<String>(0)?)?;
            validate_workspaces(&ids)?;
            let accepted = match row.get_value(2)? {
                turso::Value::Null => None,
                turso::Value::Text(value) => {
                    model::validate_device(&value)?;
                    Some(value)
                }
                _ => bail!("invalid stored invitation acceptance"),
            };
            (ids, row.get::<i64>(1)?, accepted)
        };
        let (workspace_ids, expires_at, accepted) = invitation;
        if let Some(accepted) = accepted {
            ensure!(
                accepted == peer.id,
                "invitation was already accepted by another device"
            );
            // A lost acceptance reply can be retried by the same authenticated identity after expiry.
            write_peer(&state.conn, &peer).await?;
            state.peers.insert(peer.id.clone(), peer);
            return Ok(workspace_ids);
        }
        ensure!(now_ms >= 0 && now_ms < expires_at, "invitation has expired");
        let mut operations = state.operations.clone();
        let mut grants = Vec::new();
        for workspace_id in &workspace_ids {
            // Each scope appears once; a grant cannot change another workspace's heads.
            let workspace = state
                .projection
                .workspaces
                .get(workspace_id)
                .context("invited workspace is absent")?;
            ensure!(
                workspace.members.contains(&self.device_id()),
                "this device cannot grant workspace membership"
            );
            let grant_id = format!(
                "grant-{}",
                hex::encode(Sha256::digest(
                    format!("{token_hash}\0{workspace_id}\0{}", peer.id).as_bytes()
                ))
            );
            let signed = SignedOperation::sign(
                Operation {
                    id: grant_id,
                    workspace_id: workspace_id.clone(),
                    author: self.device_id(),
                    parents: workspace.heads.clone(),
                    action: Action::GrantMember {
                        member_id: peer.id.clone(),
                    },
                },
                &self.inner.key,
            )?;
            operations.push(signed.clone());
            grants.push(signed);
        }
        let projection = model::project(&operations)?;
        let tx = state.conn.transaction().await?;
        for signed in &grants {
            insert_operation(&tx, signed).await?;
        }
        write_peer(&tx, &peer).await?;
        tx.execute(
            "UPDATE invitations SET accepted_peer = ?1 WHERE token_hash = ?2",
            (peer.id.as_str(), token_hash),
        )
        .await?;
        tx.commit().await?;
        state.install(operations, projection)?;
        state.peers.insert(peer.id.clone(), peer);
        Ok(workspace_ids)
    }
    pub async fn save_peer(&self, peer: PeerRecord) -> Result<()> {
        let owner = self.clone();
        tokio::spawn(async move { owner.save_peer_owned(peer).await })
            .await
            .context("peer contact owner ended")?
    }
    async fn save_peer_owned(&self, peer: PeerRecord) -> Result<()> {
        validate_peer(&peer)?;
        let mut state = self.inner.state.lock().await;
        let projection = &state.projection;
        ensure!(
            projection
                .workspaces
                .values()
                .any(|w| w.members.contains(&peer.id)),
            "peer has no enrolled workspace"
        );
        if state.peers.get(&peer.id) == Some(&peer) {
            return Ok(());
        }
        write_peer(&state.conn, &peer).await?;
        state.peers.insert(peer.id.clone(), peer);
        Ok(())
    }
    pub async fn peers(&self) -> Result<Vec<PeerRecord>> {
        let state = self.inner.state.lock().await;
        Ok(state.peers.values().cloned().collect())
    }
}

fn validate_token_hash(value: &str) -> Result<()> {
    ensure!(
        value.len() == 64
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid invitation hash"
    );
    Ok(())
}
fn validate_workspaces(ids: &[String]) -> Result<()> {
    ensure!(
        ids.len() <= model::MAX_WORKSPACE_SCOPE,
        "too many selected workspaces"
    );
    let mut seen = BTreeSet::new();
    for id in ids {
        model::validate_id(id)?;
        ensure!(seen.insert(id), "duplicate selected workspace");
    }
    Ok(())
}
fn validate_peer(peer: &PeerRecord) -> Result<()> {
    let id = model::validate_device(&peer.id)?;
    model::validate_device_name(&peer.name)?;
    ensure!(
        peer.address_json.len() <= 32768,
        "peer address is too large"
    );
    let address: EndpointAddr = serde_json::from_str(&peer.address_json)?;
    ensure!(address.id == id, "peer address belongs to another identity");
    Ok(())
}
async fn write_peer(conn: &Connection, peer: &PeerRecord) -> Result<()> {
    conn.execute("INSERT INTO peers(id,payload) VALUES(?1,?2) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (peer.id.as_str(), serde_json::to_string(peer)?)).await?;
    Ok(())
}
async fn load_operations(conn: &Connection) -> Result<Vec<SignedOperation>> {
    let mut rows = conn
        .query(
            "SELECT id,workspace_id,payload FROM operations ORDER BY id",
            (),
        )
        .await?;
    let mut operations = Vec::new();
    while let Some(row) = rows.next().await? {
        ensure!(
            operations.len() < model::MAX_OPERATIONS,
            "stored history exceeds supported limit"
        );
        let signed: SignedOperation = serde_json::from_str(&row.get::<String>(2)?)?;
        ensure!(
            signed.operation.id == row.get::<String>(0)?
                && signed.operation.workspace_id == row.get::<String>(1)?,
            "stored operation index disagrees with signed payload"
        );
        operations.push(signed);
    }
    Ok(operations)
}
async fn insert_operation(conn: &Connection, signed: &SignedOperation) -> Result<()> {
    conn.execute(
        "INSERT INTO operations(id,workspace_id,payload) VALUES(?1,?2,?3)",
        (
            signed.operation.id.as_str(),
            signed.operation.workspace_id.as_str(),
            serde_json::to_string(signed)?,
        ),
    )
    .await?;
    Ok(())
}
fn merge_operations(
    existing: Vec<SignedOperation>,
    incoming: &[SignedOperation],
) -> Result<(Vec<SignedOperation>, Vec<SignedOperation>, usize)> {
    ensure!(
        incoming.len() <= model::MAX_OPERATIONS,
        "incoming history exceeds supported limit"
    );
    let mut all: BTreeMap<_, _> = existing
        .into_iter()
        .map(|s| (s.operation.id.clone(), s))
        .collect();
    let mut added = Vec::new();
    let mut duplicates = 0;
    for signed in incoming {
        signed.verify()?;
        if let Some(previous) = all.get(&signed.operation.id) {
            ensure!(
                previous == signed,
                "operation identity was reused with different content"
            );
            duplicates += 1;
        } else {
            all.insert(signed.operation.id.clone(), signed.clone());
            added.push(signed.clone());
        }
    }
    ensure!(all.len() <= model::MAX_OPERATIONS, "history limit reached");
    Ok((all.into_values().collect(), added, duplicates))
}

fn workspace_fingerprints(operations: &[SignedOperation]) -> Result<BTreeMap<String, String>> {
    let mut ordered: Vec<_> = operations.iter().collect();
    ordered.sort_by_key(|s| &s.operation.id);
    let mut hashes = BTreeMap::<String, Sha256>::new();
    for signed in ordered {
        let bytes = serde_json::to_vec(signed)?;
        let hash = hashes
            .entry(signed.operation.workspace_id.clone())
            .or_default();
        hash.update((bytes.len() as u64).to_be_bytes());
        hash.update(bytes);
    }
    Ok(hashes
        .into_iter()
        .map(|(id, hash)| (id, hex::encode(hash.finalize())))
        .collect())
}
async fn load_peers(conn: &Connection) -> Result<BTreeMap<String, PeerRecord>> {
    let mut rows = conn
        .query("SELECT id,payload FROM peers ORDER BY id", ())
        .await?;
    let mut peers = BTreeMap::new();
    while let Some(row) = rows.next().await? {
        let peer: PeerRecord = serde_json::from_str(&row.get::<String>(1)?)?;
        validate_peer(&peer)?;
        ensure!(
            peer.id == row.get::<String>(0)?,
            "stored peer index disagrees with contact"
        );
        peers.insert(peer.id.clone(), peer);
    }
    Ok(peers)
}
async fn load_requests(
    conn: &Connection,
    operations: &[SignedOperation],
    device_id: &str,
) -> Result<BTreeMap<String, Command>> {
    let mut rows = conn
        .query("SELECT id,payload FROM requests ORDER BY id", ())
        .await?;
    let mut requests = BTreeMap::new();
    while let Some(row) = rows.next().await? {
        let command: Command = serde_json::from_str(&row.get::<String>(1)?)?;
        command.validate()?;
        let (id, workspace_id) = command.identity();
        ensure!(
            id == row.get::<String>(0)?,
            "stored request index disagrees with command"
        );
        let signed = operations
            .iter()
            .find(|s| s.operation.id == id)
            .context("saved request has no committed operation")?;
        ensure!(
            signed.operation.workspace_id == workspace_id
                && signed.operation.author == device_id
                && command_matches(&command, &signed.operation.action),
            "saved request disagrees with committed operation"
        );
        requests.insert(id.to_string(), command);
    }
    Ok(requests)
}
fn command_matches(command: &Command, action: &Action) -> bool {
    match (command, action) {
        (Command::CreateWorkspace { title: a, .. }, Action::CreateWorkspace { title: b }) => a == b,
        (
            Command::CreateSurface {
                resource_id: ar,
                title: at,
                surface: a,
                ..
            },
            Action::CreateSurface {
                resource_id: br,
                title: bt,
                surface: b,
            },
        ) => ar == br && at == bt && a == b,
        (
            Command::EditSurface {
                resource_id: ar,
                title: at,
                surface: a,
                expected_heads: ah,
                ..
            },
            Action::EditSurface {
                resource_id: br,
                title: bt,
                surface: b,
                expected_heads: bh,
            },
        ) => {
            ar == br
                && at == bt
                && a == b
                && ah.iter().collect::<BTreeSet<_>>() == bh.iter().collect::<BTreeSet<_>>()
        }
        (
            Command::CreateResource {
                resource_id: ar,
                title: at,
                body: ab,
                ..
            },
            Action::CreateResource {
                resource_id: br,
                title: bt,
                body: bb,
            },
        ) => ar == br && at == bt && ab == bb,
        (
            Command::EditResource {
                resource_id: ar,
                title: at,
                body: ab,
                expected_heads: ah,
                ..
            },
            Action::EditResource {
                resource_id: br,
                title: bt,
                body: bb,
                expected_heads: bh,
            },
        ) => {
            ar == br
                && at == bt
                && ab == bb
                && ah.iter().collect::<BTreeSet<_>>() == bh.iter().collect::<BTreeSet<_>>()
        }
        (
            Command::AddPlacement {
                resource_id: ar,
                source_workspace_id: aw,
                ..
            },
            Action::AddPlacement {
                resource_id: br,
                source_workspace_id: bw,
            },
        )
        | (
            Command::RemovePlacement {
                resource_id: ar,
                source_workspace_id: aw,
                ..
            },
            Action::RemovePlacement {
                resource_id: br,
                source_workspace_id: bw,
                ..
            },
        ) => ar == br && aw == bw,
        _ => false,
    }
}
