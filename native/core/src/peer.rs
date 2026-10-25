//! Authenticated peer transport. The store owns durable decisions and effects.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail, ensure};
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use iroh::endpoint::{Incoming, presets};
use iroh::{Endpoint, EndpointAddr, RelayMode, SecretKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::sync::Semaphore;
use tokio::task::{JoinHandle, JoinSet};
use tokio::time::timeout;

use crate::model::{
    ImportReceipt, MAX_WORKSPACE_SCOPE, PeerRecord, SignedOperation, validate_device_name,
};
use crate::store::Store;

const ALPN: &[u8] = b"workspace/replication/1";
const VERSION: u8 = 1;
const MAX_MESSAGE_BYTES: usize = 8 * 1024 * 1024;
const MAX_OPERATIONS: usize = 4096;
const MAX_TICKET_BYTES: usize = 12 * 1024;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_INCOMING: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkMode {
    Default,
    Loopback,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct PairingInvite {
    pub ticket: String,
    pub expires_at_ms: i64,
}

// No Debug implementation: an invitation contains a bearer enrollment secret.
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Ticket {
    version: u8,
    inviter: Contact,
    nonce: String,
    workspace_ids: Vec<String>,
    expires_at_ms: i64,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Contact {
    name: String,
    address: EndpointAddr,
}

impl Contact {
    fn record(&self) -> Result<PeerRecord> {
        self.validate()?;
        Ok(PeerRecord {
            id: self.address.id.to_string(),
            name: self.name.clone(),
            address_json: serde_json::to_string(&self.address)?,
        })
    }

    fn validate(&self) -> Result<()> {
        validate_device_name(&self.name)?;
        ensure!(self.address.addrs.len() <= 16, "too many peer addresses");
        ensure!(
            serde_json::to_vec(&self.address)?.len() <= 4096,
            "peer address too large"
        );
        Ok(())
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    version: u8,
    id: String,
    contact: Contact,
    body: RequestBody,
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum RequestBody {
    Pair {
        nonce: String,
    },
    Summary {
        workspace_ids: Vec<String>,
        fingerprint: String,
        contacts: Vec<Contact>,
    },
    Sync {
        workspace_ids: Vec<String>,
        operations: Vec<SignedOperation>,
        contacts: Vec<Contact>,
    },
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Reply {
    version: u8,
    id: String,
    contact: Contact,
    body: ReplyBody,
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum ReplyBody {
    Paired {
        workspace_ids: Vec<String>,
        operations: Vec<SignedOperation>,
        contacts: Vec<Contact>,
    },
    Synced {
        workspace_ids: Vec<String>,
        operations: Vec<SignedOperation>,
        contacts: Vec<Contact>,
    },
    Current {
        workspace_ids: Vec<String>,
        contacts: Vec<Contact>,
    },
    NeedSync,
    Rejected,
}

pub(crate) struct RunningPeer {
    pub endpoint: Endpoint,
    receiver: JoinHandle<()>,
    name: String,
    mode: NetworkMode,
}

impl RunningPeer {
    pub async fn start(store: Store, mode: NetworkMode) -> Result<Self> {
        let snapshot = store.snapshot().await?;
        let builder = match mode {
            NetworkMode::Default => Endpoint::builder(presets::N0),
            NetworkMode::Loopback => Endpoint::builder(presets::Minimal)
                .relay_mode(RelayMode::Disabled)
                .clear_address_lookup()
                .clear_ip_transports()
                .bind_addr("127.0.0.1:0")?,
        };
        let endpoint = builder
            .secret_key(store.secret_key())
            .alpns(vec![ALPN.to_vec()])
            .bind()
            .await?;
        let listener = endpoint.clone();
        let name = snapshot.device_name;
        let receiver_name = name.clone();
        let receiver = tokio::spawn(async move {
            let permits = Arc::new(Semaphore::new(MAX_INCOMING));
            let mut requests = JoinSet::new();
            while let Some(incoming) = listener.accept().await {
                let Ok(permit) = permits.clone().try_acquire_owned() else {
                    incoming.refuse();
                    continue;
                };
                let store = store.clone();
                let contact = own_contact(&listener, &receiver_name);
                requests.spawn(async move {
                    let _permit = permit;
                    // Protocol errors close this request. Never log peer data or tickets.
                    let _ = receive(incoming, store, contact).await;
                });
                while requests.try_join_next().is_some() {}
            }
            while requests.join_next().await.is_some() {}
        });
        Ok(Self {
            endpoint,
            receiver,
            name,
            mode,
        })
    }

    pub fn client(&self) -> PeerClient {
        PeerClient {
            endpoint: self.endpoint.clone(),
            name: self.name.clone(),
            mode: self.mode,
        }
    }

    pub async fn shutdown(mut self) -> Result<()> {
        self.endpoint.close().await;
        (&mut self.receiver).await.context("peer receiver failed")?;
        Ok(())
    }
}

impl Drop for RunningPeer {
    fn drop(&mut self) {
        // Explicit shutdown drains requests. Unexpected owner loss retires the
        // listener and its JoinSet instead of leaving an orphaned network task.
        self.receiver.abort();
    }
}

#[derive(Clone)]
pub(crate) struct PeerClient {
    endpoint: Endpoint,
    name: String,
    mode: NetworkMode,
}

impl PeerClient {
    pub async fn invite(
        &self,
        store: &Store,
        workspace_ids: Vec<String>,
        ttl_seconds: u32,
    ) -> Result<PairingInvite> {
        validate_workspaces(&workspace_ids)?;
        ensure!(
            (1..=900).contains(&ttl_seconds),
            "invite lifetime must be 1–900 seconds"
        );
        // A connected relay improves cross-network usability. Lack of one remains
        // a routing observation: direct addresses or identity lookup can still work.
        if self.mode == NetworkMode::Default && self.endpoint.addr().relay_urls().next().is_none() {
            let _ = timeout(Duration::from_millis(750), self.endpoint.online()).await;
        }
        let nonce = hex::encode(SecretKey::generate().to_bytes());
        let expires_at_ms = now_ms()? + i64::from(ttl_seconds) * 1000;
        store
            .create_invite(nonce_hash(&nonce)?, workspace_ids.clone(), expires_at_ms)
            .await?;
        let payload = Ticket {
            version: VERSION,
            inviter: own_contact(&self.endpoint, &self.name),
            nonce,
            workspace_ids,
            expires_at_ms,
        };
        let ticket = format!(
            "workspace1:{}",
            URL_SAFE_NO_PAD.encode(serde_json::to_vec(&payload)?)
        );
        ensure!(ticket.len() <= MAX_TICKET_BYTES, "pairing ticket too large");
        Ok(PairingInvite {
            ticket,
            expires_at_ms,
        })
    }

    pub async fn join(&self, store: &Store, encoded: &str) -> Result<ImportReceipt> {
        ensure!(
            encoded.len() <= MAX_TICKET_BYTES,
            "pairing ticket too large"
        );
        let content = encoded
            .trim()
            .strip_prefix("workspace1:")
            .context("unsupported pairing ticket")?;
        let ticket: Ticket = serde_json::from_slice(
            &URL_SAFE_NO_PAD
                .decode(content)
                .context("invalid pairing ticket encoding")?,
        )
        .context("invalid pairing ticket")?;
        ensure!(ticket.version == VERSION, "unsupported pairing version");
        ticket.inviter.validate()?;
        validate_workspaces(&ticket.workspace_ids)?;
        nonce_hash(&ticket.nonce)?;
        ensure!(
            ticket.inviter.address.id != self.endpoint.id(),
            "cannot pair a device with itself"
        );
        // Expiry is evaluated by the inviter, so an already-accepted same-device
        // retry can recover its durable grant after losing the original response.
        let reply = self
            .exchange(
                ticket.inviter.address,
                RequestBody::Pair {
                    nonce: ticket.nonce,
                },
            )
            .await?;
        let ReplyBody::Paired {
            workspace_ids,
            operations,
            contacts,
        } = reply.body
        else {
            bail!("pairing was rejected; the invite may be expired or already used");
        };
        ensure!(
            scope_set(&workspace_ids) == scope_set(&ticket.workspace_ids),
            "pairing reply changed selected workspaces"
        );
        validate_operations(&workspace_ids, &operations)?;
        let receipt = store
            .bootstrap(reply.contact.record()?, workspace_ids.clone(), operations)
            .await?;
        retain_contacts(store, &workspace_ids, contacts).await?;
        Ok(receipt)
    }

    pub async fn synchronize(&self, store: &Store, peer: PeerRecord) -> Result<ImportReceipt> {
        let address: EndpointAddr =
            serde_json::from_str(&peer.address_json).context("invalid saved peer address")?;
        ensure!(
            address.id.to_string() == peer.id,
            "saved peer identity mismatch"
        );
        let workspace_ids = store.allowed_workspaces(&peer.id).await?;
        ensure!(!workspace_ids.is_empty(), "no shared workspaces");
        let mut receipt = ImportReceipt {
            imported: 0,
            already_present: 0,
        };
        for selection in workspace_ids.chunks(MAX_WORKSPACE_SCOPE) {
            let batch = self
                .synchronize_scope(store, &peer.id, address.clone(), selection.to_vec())
                .await?;
            receipt.imported += batch.imported;
            receipt.already_present += batch.already_present;
        }
        Ok(receipt)
    }

    async fn synchronize_scope(
        &self,
        store: &Store,
        peer_id: &str,
        address: EndpointAddr,
        workspace_ids: Vec<String>,
    ) -> Result<ImportReceipt> {
        validate_workspaces(&workspace_ids)?;
        let fingerprint = store.fingerprint(&workspace_ids).await?;
        let contacts = shared_contacts(store, &workspace_ids).await?;
        let summary = self
            .exchange(
                address.clone(),
                RequestBody::Summary {
                    workspace_ids: workspace_ids.clone(),
                    fingerprint,
                    contacts: contacts.clone(),
                },
            )
            .await?;
        match summary.body {
            ReplyBody::Current {
                workspace_ids: returned,
                contacts,
            } => {
                ensure!(
                    scope_set(&returned) == scope_set(&workspace_ids),
                    "summary reply changed workspace scope"
                );
                store.save_peer(summary.contact.record()?).await?;
                retain_contacts(store, &workspace_ids, contacts).await?;
                return Ok(ImportReceipt {
                    imported: 0,
                    already_present: 0,
                });
            }
            ReplyBody::NeedSync | ReplyBody::Rejected => {}
            _ => bail!("peer returned an invalid synchronization response"),
        }
        let operations = store.operations(&workspace_ids).await?;
        validate_operations(&workspace_ids, &operations)?;
        let reply = self
            .exchange(
                address,
                RequestBody::Sync {
                    workspace_ids: workspace_ids.clone(),
                    operations,
                    contacts,
                },
            )
            .await?;
        let ReplyBody::Synced {
            workspace_ids: returned,
            operations,
            contacts,
        } = reply.body
        else {
            bail!("peer rejected workspace synchronization");
        };
        ensure!(
            scope_set(&returned) == scope_set(&workspace_ids),
            "sync reply changed workspace scope"
        );
        validate_operations(&workspace_ids, &operations)?;
        let receipt = store.import(peer_id, &operations).await?;
        store.save_peer(reply.contact.record()?).await?;
        retain_contacts(store, &workspace_ids, contacts).await?;
        Ok(receipt)
    }

    async fn exchange(&self, address: EndpointAddr, body: RequestBody) -> Result<Reply> {
        let request = Request {
            version: VERSION,
            id: hex::encode(SecretKey::generate().to_bytes()),
            contact: own_contact(&self.endpoint, &self.name),
            body,
        };
        let bytes = serde_json::to_vec(&request)?;
        ensure!(
            bytes.len() <= MAX_MESSAGE_BYTES,
            "workspace transfer exceeds the current 8 MiB limit"
        );
        timeout(REQUEST_TIMEOUT, async {
            let connect = self.endpoint.connect(address.clone(), ALPN);
            let connection = match self.mode {
                NetworkMode::Loopback => timeout(Duration::from_secs(2), connect)
                    .await
                    .context("peer did not connect before deadline")??,
                NetworkMode::Default => connect.await?,
            };
            ensure!(
                connection.remote_id() == address.id,
                "authenticated peer identity mismatch"
            );
            let result = async {
                let (mut send, mut receive) = connection.open_bi().await?;
                send.write_all(&bytes).await?;
                send.finish()?;
                let incoming = receive.read_to_end(MAX_MESSAGE_BYTES).await?;
                let reply: Reply =
                    serde_json::from_slice(&incoming).context("invalid peer reply")?;
                ensure!(
                    reply.version == VERSION && reply.id == request.id,
                    "reply does not match request"
                );
                ensure!(
                    reply.contact.address.id == address.id,
                    "reply contact changed authenticated identity"
                );
                reply.contact.validate()?;
                Ok(reply)
            }
            .await;
            connection.close(0u32.into(), b"exchange complete");
            result
        })
        .await
        .context("peer did not respond before deadline")?
    }
}

async fn receive(incoming: Incoming, store: Store, contact: Contact) -> Result<()> {
    let (connection, mut send, request) = timeout(REQUEST_TIMEOUT, async {
        let connection = incoming.await?;
        let (send, mut receive) = connection.accept_bi().await?;
        let bytes = receive.read_to_end(MAX_MESSAGE_BYTES).await?;
        let request: Request = serde_json::from_slice(&bytes).context("invalid peer request")?;
        Ok::<_, anyhow::Error>((connection, send, request))
    })
    .await
    .context("incoming request deadline elapsed")??;
    let remote_id = connection.remote_id().to_string();
    let id = request.id.clone();
    let body = match handle_request(&store, &remote_id, request).await {
        Ok(body) => body,
        Err(_) => ReplyBody::Rejected,
    };
    let reply = Reply {
        version: VERSION,
        id,
        contact,
        body,
    };
    let bytes = serde_json::to_vec(&reply)?;
    ensure!(
        bytes.len() <= MAX_MESSAGE_BYTES,
        "workspace transfer exceeds the current 8 MiB limit"
    );
    timeout(REQUEST_TIMEOUT, async {
        send.write_all(&bytes).await?;
        send.finish()?;
        // Retain the response until the requester reads it and closes. A lost
        // response never rolls back or repeats a committed local transaction.
        connection.closed().await;
        Ok::<_, anyhow::Error>(())
    })
    .await
    .context("response deadline elapsed")??;
    Ok(())
}

async fn handle_request(
    store: &Store,
    authenticated_id: &str,
    request: Request,
) -> Result<ReplyBody> {
    ensure!(request.version == VERSION, "unsupported protocol version");
    ensure!(
        request.id.len() == 64 && hex::decode(&request.id)?.len() == 32,
        "invalid request identity"
    );
    ensure!(
        request.contact.address.id.to_string() == authenticated_id,
        "request contact impersonates another endpoint"
    );
    let contact = request.contact.record()?;
    match request.body {
        RequestBody::Pair { nonce } => {
            let workspace_ids = store
                .accept_invite(&nonce_hash(&nonce)?, contact, now_ms()?)
                .await?;
            let operations = store.operations(&workspace_ids).await?;
            validate_operations(&workspace_ids, &operations)?;
            let contacts = shared_contacts(store, &workspace_ids).await?;
            Ok(ReplyBody::Paired {
                workspace_ids,
                operations,
                contacts,
            })
        }
        RequestBody::Summary {
            workspace_ids,
            fingerprint,
            contacts,
        } => {
            validate_workspaces(&workspace_ids)?;
            ensure!(
                fingerprint.len() == 64 && hex::decode(&fingerprint)?.len() == 32,
                "invalid history fingerprint"
            );
            let allowed = scope_set(&store.allowed_workspaces(authenticated_id).await?);
            // A new member must supply its full anchored grant proof through
            // Sync. Summary alone never authorizes any workspace observation.
            ensure!(
                workspace_ids.iter().all(|id| allowed.contains(id)),
                "peer is not authorized for every requested workspace"
            );
            if store.fingerprint(&workspace_ids).await? != fingerprint {
                return Ok(ReplyBody::NeedSync);
            }
            store.save_peer(contact).await?;
            retain_contacts(store, &workspace_ids, contacts).await?;
            let contacts = shared_contacts(store, &workspace_ids).await?;
            Ok(ReplyBody::Current {
                workspace_ids,
                contacts,
            })
        }
        RequestBody::Sync {
            workspace_ids,
            operations,
            contacts,
        } => {
            validate_workspaces(&workspace_ids)?;
            validate_operations(&workspace_ids, &operations)?;
            store.import(authenticated_id, &operations).await?;
            let allowed = scope_set(&store.allowed_workspaces(authenticated_id).await?);
            ensure!(
                workspace_ids.iter().all(|id| allowed.contains(id)),
                "peer is not authorized for every requested workspace"
            );
            store.save_peer(contact).await?;
            retain_contacts(store, &workspace_ids, contacts).await?;
            let operations = store.operations(&workspace_ids).await?;
            validate_operations(&workspace_ids, &operations)?;
            let contacts = shared_contacts(store, &workspace_ids).await?;
            Ok(ReplyBody::Synced {
                workspace_ids,
                operations,
                contacts,
            })
        }
    }
}

async fn shared_contacts(store: &Store, workspaces: &[String]) -> Result<Vec<Contact>> {
    let scopes = scope_set(workspaces);
    let mut result = Vec::new();
    for peer in store.peers().await? {
        if store
            .allowed_workspaces(&peer.id)
            .await?
            .iter()
            .any(|id| scopes.contains(id))
        {
            let contact = Contact {
                name: peer.name,
                address: serde_json::from_str(&peer.address_json)?,
            };
            contact.validate()?;
            result.push(contact);
        }
    }
    ensure!(
        result.len() <= 64,
        "too many peer contacts for this transfer"
    );
    Ok(result)
}

async fn retain_contacts(
    store: &Store,
    workspaces: &[String],
    contacts: Vec<Contact>,
) -> Result<()> {
    ensure!(
        contacts.len() <= 64,
        "too many peer contacts for this transfer"
    );
    let scopes = scope_set(workspaces);
    for contact in &contacts {
        contact.validate()?;
        let allowed = store
            .allowed_workspaces(&contact.address.id.to_string())
            .await?;
        ensure!(
            allowed.iter().any(|id| scopes.contains(id)),
            "contact is not a member of selected workspaces"
        );
    }
    let own_id = store.device_id();
    for contact in contacts {
        if contact.address.id.to_string() != own_id {
            store.save_peer(contact.record()?).await?;
        }
    }
    Ok(())
}

fn own_contact(endpoint: &Endpoint, name: &str) -> Contact {
    let mut address = endpoint.addr();
    // Explicit loopback bind addresses are immediately usable, even before
    // the network-monitor observation updates Endpoint::addr().
    for socket in endpoint.bound_sockets() {
        if !socket.ip().is_unspecified() {
            address = address.with_ip_addr(socket);
        }
    }
    Contact {
        name: name.to_owned(),
        address,
    }
}

fn scope_set(values: &[String]) -> BTreeSet<String> {
    values.iter().cloned().collect()
}

fn validate_workspaces(values: &[String]) -> Result<()> {
    ensure!(
        !values.is_empty() && values.len() <= MAX_WORKSPACE_SCOPE,
        "invalid workspace selection"
    );
    ensure!(
        values.iter().all(|id| !id.is_empty() && id.len() <= 160),
        "invalid workspace identity"
    );
    ensure!(
        scope_set(values).len() == values.len(),
        "duplicate workspace selection"
    );
    Ok(())
}

fn validate_operations(workspaces: &[String], operations: &[SignedOperation]) -> Result<()> {
    ensure!(
        operations.len() <= MAX_OPERATIONS,
        "workspace transfer exceeds the current operation limit"
    );
    let scopes = scope_set(workspaces);
    ensure!(
        operations
            .iter()
            .all(|op| scopes.contains(&op.operation.workspace_id)),
        "operation is outside selected workspaces"
    );
    Ok(())
}

fn nonce_hash(nonce: &str) -> Result<String> {
    ensure!(nonce.len() == 64, "invalid pairing secret");
    let bytes = hex::decode(nonce).context("invalid pairing secret")?;
    ensure!(bytes.len() == 32, "invalid pairing secret");
    Ok(hex::encode(Sha256::digest(bytes)))
}

pub(crate) fn now_ms() -> Result<i64> {
    i64::try_from(SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis())
        .context("clock outside supported range")
}
