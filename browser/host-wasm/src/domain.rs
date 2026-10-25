//! Synchronous domain ownership, without persistence, clocks, or network effects.
//! A prepared state becomes visible only after its durable owner calls commit.

use crate::model::{
    self, Action, Command, CommandReceipt, ImportReceipt, Operation, PeerRecord, SignedOperation,
    Snapshot,
};
use anyhow::{Context, Result, ensure};
use iroh::{EndpointAddr, SecretKey, Signature};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use wasm_bindgen::prelude::*;

const MAX_JSON_BYTES: usize = model::MAX_HISTORY_BYTES + model::MAX_OPERATIONS + 2;

struct State {
    operations: Vec<SignedOperation>,
    projection: model::Projection,
    requests: BTreeMap<String, Command>,
    fingerprints: BTreeMap<String, String>,
}

impl State {
    fn new(
        operations: Vec<SignedOperation>,
        projection: model::Projection,
        requests: BTreeMap<String, Command>,
    ) -> Result<Self> {
        // Prepare every derived value before the durable owner can commit.
        // Publishing an acknowledged batch then only moves this validated state.
        let fingerprints = workspace_fingerprints(&operations)?;
        Ok(Self {
            operations,
            projection,
            requests,
            fingerprints,
        })
    }
}

struct Pending {
    id: String,
    state: State,
}

#[derive(Serialize)]
#[serde(untagged)]
enum Receipt {
    Command(CommandReceipt),
    Import(ImportReceipt),
}

#[derive(Serialize)]
struct Batch {
    batch_id: Option<String>,
    operations: Vec<SignedOperation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    request: Option<Command>,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt: Option<Receipt>,
}

impl Batch {
    fn reply(receipt: Receipt) -> Self {
        Self {
            batch_id: None,
            operations: Vec::new(),
            request: None,
            receipt: Some(receipt),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Contact {
    name: String,
    address: EndpointAddr,
}

#[wasm_bindgen]
pub struct Domain {
    key: SecretKey,
    name: String,
    state: State,
    pending: Option<Pending>,
    next_batch: u64,
}

#[wasm_bindgen]
impl Domain {
    #[wasm_bindgen(constructor)]
    pub fn new(
        seed_hex: &str,
        device_name: &str,
        operations_json: &str,
        requests_json: &str,
    ) -> Result<Domain, String> {
        Self::open(seed_hex, device_name, operations_json, requests_json).map_err(message)
    }

    pub fn id(&self) -> String {
        self.key.public().to_string()
    }

    pub fn snapshot(&self) -> Result<String, String> {
        json(&Snapshot {
            device_id: self.id(),
            device_name: self.name.clone(),
            workspaces: self.state.projection.workspaces.values().cloned().collect(),
            resources: self.state.projection.resources.values().cloned().collect(),
            operation_count: self.state.operations.len(),
        })
    }

    pub fn prepare_command(&mut self, command_json: &str) -> Result<String, String> {
        self.command(command_json).map_err(message)
    }

    /// A separate, domain-separated signature lane. Payloads never enter the
    /// ordinary operation history or snapshot. Callers retain encrypted records.
    pub fn sign_connection(&self, workspace_id: &str, payload: &str) -> Result<String, String> {
        self.connection_bytes(workspace_id, &self.id(), payload)
            .map(|bytes| hex::encode(self.key.sign(&bytes).to_bytes()))
            .map_err(|_| "workspace connection cannot be signed".into())
    }

    pub fn verify_connection(
        &self,
        workspace_id: &str,
        author: &str,
        payload: &str,
        signature: &str,
    ) -> Result<(), String> {
        let verify = || -> Result<()> {
            let bytes = self.connection_bytes(workspace_id, author, payload)?;
            ensure!(signature.len() == 128, "invalid connection signature");
            let mut raw = [0u8; 64];
            hex::decode_to_slice(signature, &mut raw)?;
            model::validate_device(author)?.verify(&bytes, &Signature::from_bytes(&raw))?;
            Ok(())
        };
        verify().map_err(|_| "workspace connection signature or membership is invalid".into())
    }

    pub fn prepare_grant(
        &mut self,
        token_hash: &str,
        scopes_json: &str,
        peer_id: &str,
    ) -> Result<String, String> {
        self.grant(token_hash, scopes_json, peer_id)
            .map_err(message)
    }

    pub fn prepare_import(
        &mut self,
        authenticated_peer_id: &str,
        incoming_json: &str,
        bootstrap_scopes_json: Option<String>,
    ) -> Result<String, String> {
        self.import(
            authenticated_peer_id,
            incoming_json,
            bootstrap_scopes_json.as_deref(),
        )
        .map_err(message)
    }

    pub fn prepare_bootstrap(
        &mut self,
        authenticated_peer_id: &str,
        scopes_json: &str,
        incoming_json: &str,
    ) -> Result<String, String> {
        self.import(authenticated_peer_id, incoming_json, Some(scopes_json))
            .map_err(message)
    }

    pub fn commit(&mut self, batch_id: &str) -> Result<(), String> {
        self.check_batch(batch_id).map_err(message)?;
        let pending = self
            .pending
            .take()
            .expect("the exact pending batch was checked");
        self.state = pending.state;
        Ok(())
    }

    pub fn discard(&mut self, batch_id: &str) -> Result<(), String> {
        self.check_batch(batch_id).map_err(message)?;
        self.pending = None;
        Ok(())
    }

    /// Local reads still require this host to belong to every selected workspace.
    /// Peer responses must use export_for, which also authenticates the receiver.
    pub fn operations(&self, scopes_json: &str) -> Result<String, String> {
        let scopes = scopes(scopes_json).map_err(message)?;
        self.check_scope_membership(&self.id(), &scopes)
            .map_err(message)?;
        json(&self.selected_operations(&scopes))
    }

    pub fn export_for(&self, peer_id: &str, scopes_json: &str) -> Result<String, String> {
        model::validate_device(peer_id).map_err(message)?;
        let scopes = scopes(scopes_json).map_err(message)?;
        // This check is independent of how many operations a peer sent. An
        // empty incoming batch can never bypass outbound authorization.
        self.check_scope_membership(peer_id, &scopes)
            .map_err(message)?;
        self.check_scope_membership(&self.id(), &scopes)
            .map_err(message)?;
        json(&self.selected_operations(&scopes))
    }

    pub fn allowed_workspaces(&self, peer_id: &str) -> Result<String, String> {
        model::validate_device(peer_id).map_err(message)?;
        let ids: Vec<_> = self
            .state
            .projection
            .workspaces
            .values()
            .filter(|workspace| workspace.members.iter().any(|id| id == peer_id))
            .map(|workspace| &workspace.id)
            .collect();
        json(&ids)
    }

    pub fn fingerprint(&self, scopes_json: &str) -> Result<String, String> {
        self.fingerprint_scopes(scopes_json).map_err(message)
    }

    /// Validate an untrusted contact without claiming workspace enrollment.
    pub fn validate_contact(&self, contact_json: &str) -> Result<String, String> {
        let contact: Contact = parse(contact_json, 65_536).map_err(message)?;
        let peer = PeerRecord {
            id: contact.address.id.to_string(),
            name: contact.name,
            address_json: serde_json::to_string(&contact.address)
                .map_err(|error| error.to_string())?,
        };
        validate_peer_record(&peer).map_err(message)?;
        json(&peer)
    }

    /// Native Store::save_peer also requires a previously enrolled workspace.
    pub fn validate_peer(&self, peer_json: &str) -> Result<String, String> {
        let peer: PeerRecord = parse(peer_json, 65_536).map_err(message)?;
        validate_peer_record(&peer).map_err(message)?;
        ensure_member_contact(&self.state.projection, &peer.id).map_err(message)?;
        json(&peer)
    }

    pub fn validate_device_name(&self, value: &str) -> Result<(), String> {
        model::validate_device_name(value).map_err(message)
    }
}

impl Domain {
    fn connection_bytes(&self, workspace_id: &str, author: &str, payload: &str) -> Result<Vec<u8>> {
        ensure!(
            !payload.is_empty() && payload.len() <= 16_384,
            "invalid connection payload size"
        );
        let selected = vec![workspace_id.to_string()];
        self.check_scope_membership(&self.id(), &selected)?;
        self.check_scope_membership(author, &selected)?;
        Ok(serde_json::to_vec(&(
            "shared-workspace/connection-signature/1",
            workspace_id,
            payload,
        ))?)
    }

    fn open(
        seed_hex: &str,
        name: &str,
        operations_json: &str,
        requests_json: &str,
    ) -> Result<Self> {
        ensure!(
            seed_hex.len() == 64,
            "device seed must contain exactly 32 bytes"
        );
        let mut seed = [0u8; 32];
        hex::decode_to_slice(seed_hex, &mut seed).context("invalid hexadecimal device seed")?;
        let key = SecretKey::from_bytes(&seed);
        model::validate_device_name(name)?;
        let operations: Vec<SignedOperation> = parse(operations_json, MAX_JSON_BYTES)?;
        let projection = model::project(&operations)?;
        let retained_requests: Vec<Command> = parse(requests_json, MAX_JSON_BYTES)?;
        ensure!(
            retained_requests.len() <= model::MAX_OPERATIONS,
            "too many retained requests"
        );
        let requests =
            validate_requests(retained_requests, &operations, &key.public().to_string())?;
        Ok(Self {
            key,
            name: name.into(),
            state: State::new(operations, projection, requests)?,
            pending: None,
            next_batch: 0,
        })
    }

    fn idle(&self) -> Result<()> {
        ensure!(
            self.pending.is_none(),
            "a prepared change is still awaiting durable commit"
        );
        Ok(())
    }

    fn check_batch(&self, id: &str) -> Result<()> {
        let pending = self
            .pending
            .as_ref()
            .context("there is no prepared change")?;
        ensure!(pending.id == id, "the prepared change token is stale");
        Ok(())
    }

    fn stage(
        &mut self,
        state: State,
        operations: Vec<SignedOperation>,
        request: Option<Command>,
        receipt: Option<Receipt>,
    ) -> Result<String> {
        self.idle()?;
        let generation = self
            .next_batch
            .checked_add(1)
            .context("prepared change counter exhausted")?;
        let id = format!("batch-{generation}");
        let result = serde_json::to_string(&Batch {
            batch_id: Some(id.clone()),
            operations,
            request,
            receipt,
        })?;
        // Serialization must succeed before installing the pending candidate.
        self.next_batch = generation;
        self.pending = Some(Pending { id, state });
        Ok(result)
    }

    fn command(&mut self, source: &str) -> Result<String> {
        self.idle()?;
        let command: Command = parse(source, MAX_JSON_BYTES)?;
        let (id, workspace_id) = command.identity();
        let prepared = model::prepare_command(
            &command,
            &self.key,
            &self.state.operations,
            &self.state.projection,
            &self.state.requests,
        );
        let write = match prepared {
            Ok(model::PreparedCommand::Write(write)) => write,
            Ok(model::PreparedCommand::Reply(reply)) => {
                return Ok(serde_json::to_string(&Batch::reply(Receipt::Command(
                    reply,
                )))?);
            }
            Err(error) => {
                return Ok(serde_json::to_string(&Batch::reply(Receipt::Command(
                    CommandReceipt::Rejected {
                        operation_id: id.into(),
                        workspace_id: workspace_id.into(),
                        message: error.to_string(),
                    },
                )))?);
            }
        };
        let request: Command = serde_json::from_str(&write.request_payload)?;
        let mut requests = self.state.requests.clone();
        requests.insert(id.into(), request.clone());
        let receipt = CommandReceipt::Applied {
            operation_id: id.into(),
            workspace_id: workspace_id.into(),
            replayed: false,
        };
        self.stage(
            State::new(write.operations, write.projection, requests)?,
            vec![write.signed],
            Some(request),
            Some(Receipt::Command(receipt)),
        )
    }

    fn grant(&mut self, token_hash: &str, scope_json: &str, peer_id: &str) -> Result<String> {
        self.idle()?;
        validate_token_hash(token_hash)?;
        model::validate_device(peer_id)?;
        ensure!(
            peer_id != self.id(),
            "cannot enroll this device as another peer"
        );
        let scopes = scopes(scope_json)?;
        ensure!(!scopes.is_empty(), "invitation contains no workspaces");
        self.check_scope_membership(&self.id(), &scopes)?;
        let mut grants = Vec::new();
        for workspace_id in scopes {
            let grant_id = format!(
                "grant-{}",
                hex::encode(Sha256::digest(
                    format!("{token_hash}\0{workspace_id}\0{peer_id}").as_bytes()
                ))
            );
            if let Some(previous) = self
                .state
                .operations
                .iter()
                .find(|op| op.operation.id == grant_id)
            {
                ensure!(
                    previous.operation.workspace_id == workspace_id
                        && previous.operation.author == self.id()
                        && previous.operation.action
                            == Action::GrantMember {
                                member_id: peer_id.into()
                            },
                    "grant identity was already used with different content"
                );
                continue;
            }
            let workspace = &self.state.projection.workspaces[&workspace_id];
            grants.push(SignedOperation::sign(
                Operation {
                    id: grant_id,
                    workspace_id,
                    author: self.id(),
                    parents: workspace.heads.clone(),
                    action: Action::GrantMember {
                        member_id: peer_id.into(),
                    },
                },
                &self.key,
            )?);
        }
        if grants.is_empty() {
            return Ok(serde_json::to_string(&Batch {
                batch_id: None,
                operations: Vec::new(),
                request: None,
                receipt: None,
            })?);
        }
        let mut operations = self.state.operations.clone();
        operations.extend(grants.iter().cloned());
        let projection = model::project(&operations)?;
        self.stage(
            State::new(operations, projection, self.state.requests.clone())?,
            grants,
            None,
            None,
        )
    }

    fn import(
        &mut self,
        peer: &str,
        incoming_json: &str,
        bootstrap: Option<&str>,
    ) -> Result<String> {
        self.idle()?;
        model::validate_device(peer)?;
        let incoming: Vec<SignedOperation> = parse(incoming_json, MAX_JSON_BYTES)?;
        let bootstrap_scopes = bootstrap.map(scopes).transpose()?;
        if let Some(selected) = &bootstrap_scopes {
            ensure!(!selected.is_empty(), "invitation contains no workspaces");
            ensure!(
                incoming
                    .iter()
                    .all(|op| selected.contains(&op.operation.workspace_id)),
                "bootstrap contains an unselected workspace"
            );
        } else {
            // Existing enrollment anchors every normal import. A peer cannot
            // introduce its own unrelated root and grant in the same batch.
            for signed in &incoming {
                self.check_scope_membership(
                    &self.id(),
                    std::slice::from_ref(&signed.operation.workspace_id),
                )?;
            }
        }
        let (operations, added, duplicates) = merge_operations(&self.state.operations, &incoming)?;
        let projection = if added.is_empty() {
            self.state.projection.clone()
        } else {
            model::project(&operations)?
        };
        if let Some(selected) = &bootstrap_scopes {
            for id in selected {
                let workspace = projection
                    .workspaces
                    .get(id)
                    .context("bootstrap omits a selected workspace")?;
                ensure!(
                    workspace.members.iter().any(|id| id == peer),
                    "inviter is not a workspace member"
                );
                ensure!(
                    workspace.members.contains(&self.id()),
                    "invitation does not grant this device membership"
                );
            }
        } else {
            // A new member may carry a retained member's signed grant, exactly
            // as native Store::import permits after validating the causal log.
            for signed in &incoming {
                let workspace = &projection.workspaces[&signed.operation.workspace_id];
                ensure!(
                    workspace.members.iter().any(|id| id == peer),
                    "authenticated peer is not a workspace member"
                );
            }
        }
        let receipt = Receipt::Import(ImportReceipt {
            imported: added.len(),
            already_present: duplicates,
        });
        if added.is_empty() {
            return Ok(serde_json::to_string(&Batch::reply(receipt))?);
        }
        self.stage(
            State::new(operations, projection, self.state.requests.clone())?,
            added,
            None,
            Some(receipt),
        )
    }

    fn check_scope_membership(&self, peer: &str, selected: &[String]) -> Result<()> {
        for id in selected {
            let workspace = self
                .state
                .projection
                .workspaces
                .get(id)
                .context("selected workspace is absent")?;
            ensure!(
                workspace.members.iter().any(|id| id == peer),
                "device is not a selected workspace member"
            );
        }
        Ok(())
    }

    fn selected_operations(&self, scopes: &[String]) -> Vec<&SignedOperation> {
        self.state
            .operations
            .iter()
            .filter(|signed| scopes.contains(&signed.operation.workspace_id))
            .collect()
    }

    fn fingerprint_scopes(&self, source: &str) -> Result<String> {
        let mut selected = scopes(source)?;
        selected.sort();
        let values: Vec<_> = selected
            .iter()
            .map(|id| {
                self.state
                    .fingerprints
                    .get(id)
                    .map(|fingerprint| (id, fingerprint))
                    .context("selected workspace is absent")
            })
            .collect::<Result<_>>()?;
        Ok(hex::encode(Sha256::digest(serde_json::to_vec(&values)?)))
    }
}

fn workspace_fingerprints(operations: &[SignedOperation]) -> Result<BTreeMap<String, String>> {
    let mut ordered: Vec<_> = operations.iter().collect();
    ordered.sort_by_key(|signed| &signed.operation.id);
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

fn parse<T: for<'de> Deserialize<'de>>(source: &str, maximum: usize) -> Result<T> {
    ensure!(
        source.len() <= maximum,
        "JSON input exceeds the supported size"
    );
    Ok(serde_json::from_str(source)?)
}

fn json(value: &impl Serialize) -> Result<String, String> {
    serde_json::to_string(value).map_err(|error| error.to_string())
}

fn message(error: anyhow::Error) -> String {
    error.to_string()
}

fn scopes(source: &str) -> Result<Vec<String>> {
    let ids: Vec<String> = parse(source, 8192)?;
    ensure!(
        ids.len() <= model::MAX_WORKSPACE_SCOPE,
        "too many selected workspaces"
    );
    let mut seen = BTreeSet::new();
    for id in &ids {
        model::validate_id(id)?;
        ensure!(seen.insert(id), "duplicate selected workspace");
    }
    Ok(ids)
}

fn validate_token_hash(value: &str) -> Result<()> {
    ensure!(
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
        "invalid invitation hash"
    );
    Ok(())
}

fn validate_peer_record(peer: &PeerRecord) -> Result<()> {
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

fn ensure_member_contact(projection: &model::Projection, id: &str) -> Result<()> {
    ensure!(
        projection
            .workspaces
            .values()
            .any(|workspace| workspace.members.iter().any(|member| member == id)),
        "peer has no enrolled workspace"
    );
    Ok(())
}

fn validate_requests(
    commands: Vec<Command>,
    operations: &[SignedOperation],
    device_id: &str,
) -> Result<BTreeMap<String, Command>> {
    let mut requests = BTreeMap::new();
    for command in commands {
        command.validate()?;
        let (id, workspace_id) = command.identity();
        let signed = operations
            .iter()
            .find(|signed| signed.operation.id == id)
            .context("saved request has no committed operation")?;
        ensure!(
            signed.operation.workspace_id == workspace_id
                && signed.operation.author == device_id
                && command_matches(&command, &signed.operation.action),
            "saved request disagrees with committed operation"
        );
        ensure!(
            !requests.contains_key(id),
            "duplicate retained request identity"
        );
        requests.insert(id.into(), command);
    }
    Ok(requests)
}

/// Native Store's retained-request check. The operation's signed action is the
/// authority; a separately persisted receipt cannot create successful replay.
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

fn merge_operations(
    existing: &[SignedOperation],
    incoming: &[SignedOperation],
) -> Result<(Vec<SignedOperation>, Vec<SignedOperation>, usize)> {
    ensure!(
        incoming.len() <= model::MAX_OPERATIONS,
        "incoming history exceeds supported limit"
    );
    let mut all: BTreeMap<_, _> = existing
        .iter()
        .map(|signed| (signed.operation.id.clone(), signed.clone()))
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    // Fixed synthetic fixtures, never real device identities.
    fn domain(seed: u8) -> Domain {
        Domain::new(&hex::encode([seed; 32]), "Test device", "[]", "[]").unwrap()
    }

    fn snapshot(domain: &Domain) -> Snapshot {
        serde_json::from_str(&domain.snapshot().unwrap()).unwrap()
    }

    fn prepare(domain: &mut Domain, command: Value) -> Value {
        serde_json::from_str(&domain.prepare_command(&command.to_string()).unwrap()).unwrap()
    }

    fn commit(domain: &mut Domain, batch: &Value) {
        domain.commit(batch["batch_id"].as_str().unwrap()).unwrap();
    }

    fn workspace(id: &str) -> Value {
        json!({"type":"create_workspace", "operation_id":format!("create-{id}"),
            "workspace_id":id,"title":"Synthetic workspace"})
    }

    fn create(domain: &mut Domain, id: &str) -> Value {
        let batch = prepare(domain, workspace(id));
        commit(domain, &batch);
        batch
    }

    fn grant(domain: &mut Domain, peer: &str, token: u8) -> Value {
        let batch: Value = serde_json::from_str(
            &domain
                .prepare_grant(&hex::encode([token; 32]), "[\"work\"]", peer)
                .unwrap(),
        )
        .unwrap();
        commit(domain, &batch);
        batch
    }

    fn enroll(inviter: &Domain, receiver: &mut Domain) {
        let operations = inviter.export_for(&receiver.id(), "[\"work\"]").unwrap();
        let batch: Value = serde_json::from_str(
            &receiver
                .prepare_bootstrap(&inviter.id(), "[\"work\"]", &operations)
                .unwrap(),
        )
        .unwrap();
        commit(receiver, &batch);
    }

    #[test]
    fn surface_requests_reopen_with_exact_payload_and_stale_heads_stay_conflicts() {
        let mut owner = domain(1);
        let root = create(&mut owner, "work");
        let create_command = json!({"type":"create_surface","operation_id":"code-created",
            "workspace_id":"work","resource_id":"code","title":"script.py",
            "surface":{"type":"code_document","language":"python","text":"print(1)\n"}});
        let created = prepare(&mut owner, create_command.clone());
        commit(&mut owner, &created);
        let edit_command = json!({"type":"edit_surface","operation_id":"code-edited",
            "workspace_id":"work","resource_id":"code","expected_heads":["code-created"],"title":"script.py",
            "surface":{"type":"code_document","language":"python","text":"print(2)\n"}});
        let edited = prepare(&mut owner, edit_command.clone());
        commit(&mut owner, &edited);
        let requests = json!([root["request"], created["request"], edited["request"]]);
        let operations = owner.operations("[\"work\"]").unwrap();
        let mut reopened = Domain::new(
            &hex::encode([1; 32]),
            "Restored",
            &operations,
            &requests.to_string(),
        )
        .unwrap();
        assert_eq!(snapshot(&reopened).resources, snapshot(&owner).resources);
        assert_eq!(
            prepare(&mut reopened, create_command)["receipt"]["replayed"],
            true
        );
        assert_eq!(
            prepare(&mut reopened, edit_command.clone())["receipt"]["replayed"],
            true
        );
        let mut stale = edit_command;
        stale["operation_id"] = "stale-code-edit".into();
        assert_eq!(prepare(&mut reopened, stale)["receipt"]["type"], "conflict");
        let mut forged = requests;
        forged[2]["surface"]["text"] = "Forged code".into();
        assert!(
            Domain::new(
                &hex::encode([1; 32]),
                "Restored",
                &operations,
                &forged.to_string()
            )
            .is_err()
        );
    }

    #[test]
    fn connection_signatures_require_membership_and_bind_workspace_author_and_payload() {
        let mut alice = domain(1);
        let mut bob = domain(2);
        create(&mut alice, "work");
        create(&mut alice, "private");
        let payload = "SYNTHETIC_ONLY_CONNECTION_RECORD";
        assert!(bob.sign_connection("work", payload).is_err());
        grant(&mut alice, &bob.id(), 17);
        enroll(&alice, &mut bob);
        let signature = alice.sign_connection("work", payload).unwrap();
        assert!(
            bob.verify_connection("work", &alice.id(), payload, &signature)
                .is_ok()
        );
        assert!(
            alice
                .verify_connection("private", &alice.id(), payload, &signature)
                .is_err()
        );
        assert!(
            bob.verify_connection("work", &bob.id(), payload, &signature)
                .is_err()
        );
        assert!(
            bob.verify_connection("work", &alice.id(), "TAMPERED_SYNTHETIC_RECORD", &signature)
                .is_err()
        );
        let retained = alice.export_for(&bob.id(), "[\"work\"]").unwrap();
        let outsider = Domain::new(&hex::encode([3; 32]), "Unenrolled", &retained, "[]").unwrap();
        assert!(
            outsider
                .verify_connection("work", &alice.id(), payload, &signature)
                .is_err()
        );
        assert!(outsider.sign_connection("work", payload).is_err());
        // An outsider can sign bytes without using our guarded helper. A valid
        // signature alone must not turn that device into a workspace member.
        let outsider_key = SecretKey::from_bytes(&[3; 32]);
        let unauthorized_bytes =
            serde_json::to_vec(&("shared-workspace/connection-signature/1", "work", payload))
                .unwrap();
        let unauthorized = hex::encode(outsider_key.sign(&unauthorized_bytes).to_bytes());
        assert!(
            alice
                .verify_connection("work", &outsider.id(), payload, &unauthorized)
                .is_err()
        );
        let ordinary: serde_json::Value =
            serde_json::from_str(&alice.operations("[\"work\"]").unwrap()).unwrap();
        assert!(
            alice
                .verify_connection(
                    "work",
                    &alice.id(),
                    &ordinary[0]["operation"].to_string(),
                    ordinary[0]["signature"].as_str().unwrap(),
                )
                .is_err()
        );
        assert_eq!(alice.snapshot().unwrap().contains(payload), false);
        assert_eq!(
            alice.operations("[\"work\"]").unwrap().contains(payload),
            false
        );
    }

    #[test]
    fn publication_requires_exact_durable_commit_and_retry_keeps_its_identity() {
        let mut owner = domain(1);
        let first = prepare(&mut owner, workspace("work"));
        let first_id = first["batch_id"].as_str().unwrap();
        assert_eq!(snapshot(&owner).operation_count, 0);
        assert!(
            owner
                .prepare_command(&workspace("other").to_string())
                .is_err()
        );
        assert!(owner.commit("wrong-batch").is_err());
        assert!(owner.discard("wrong-batch").is_err());
        assert_eq!(snapshot(&owner).operation_count, 0);
        owner.discard(first_id).unwrap();
        let second = prepare(&mut owner, workspace("work"));
        assert_ne!(first["batch_id"], second["batch_id"]);
        assert!(owner.commit(first_id).is_err());
        commit(&mut owner, &second);
        assert_eq!(snapshot(&owner).operation_count, 1);
        assert!(owner.commit(second["batch_id"].as_str().unwrap()).is_err());
        let replay = prepare(&mut owner, workspace("work"));
        assert!(replay["batch_id"].is_null());
        assert_eq!(replay["receipt"]["replayed"], true);
        let mut changed = workspace("work");
        changed["title"] = "Changed content".into();
        let rejected = prepare(&mut owner, changed);
        assert!(rejected["batch_id"].is_null());
        assert_eq!(rejected["receipt"]["type"], "rejected");
        assert_eq!(snapshot(&owner).operation_count, 1);
    }

    #[test]
    fn retained_requests_cannot_invent_replay_or_claim_another_authors_operation() {
        let mut owner = domain(1);
        let batch = create(&mut owner, "work");
        let operations = batch["operations"].to_string();
        let request = batch["request"].clone();
        let requests = json!([request.clone()]).to_string();
        let mut reopened = Domain::new(
            &hex::encode([1; 32]),
            "Restored device",
            &operations,
            &requests,
        )
        .unwrap();
        assert_eq!(
            prepare(&mut reopened, workspace("work"))["receipt"]["replayed"],
            true
        );
        assert!(
            Domain::new(
                &hex::encode([2; 32]),
                "Other device",
                &operations,
                &requests
            )
            .is_err()
        );
        assert!(Domain::new(&hex::encode([1; 32]), "Test", "[]", &requests).is_err());
        let mut forged = request.clone();
        forged["title"] = "Forged success".into();
        assert!(
            Domain::new(
                &hex::encode([1; 32]),
                "Test",
                &operations,
                &json!([forged]).to_string()
            )
            .is_err()
        );
        assert!(
            Domain::new(
                &hex::encode([1; 32]),
                "Test",
                &operations,
                &json!([request.clone(), request]).to_string()
            )
            .is_err()
        );
    }

    #[test]
    fn selected_export_checks_both_members_even_after_an_empty_import() {
        let mut alice = domain(1);
        let mut bob = domain(2);
        let outsider = domain(3);
        create(&mut alice, "work");
        create(&mut alice, "private");
        assert!(alice.export_for(&bob.id(), "[\"work\"]").is_err());
        let before_grant = alice.fingerprint("[\"work\"]").unwrap();
        let staged: Value = serde_json::from_str(
            &alice
                .prepare_grant(&hex::encode([7; 32]), "[\"work\"]", &bob.id())
                .unwrap(),
        )
        .unwrap();
        assert!(alice.export_for(&bob.id(), "[\"work\"]").is_err());
        assert_eq!(alice.fingerprint("[\"work\"]").unwrap(), before_grant);
        commit(&mut alice, &staged);
        assert_ne!(alice.fingerprint("[\"work\"]").unwrap(), before_grant);
        enroll(&alice, &mut bob);
        let empty: Value =
            serde_json::from_str(&alice.prepare_import(&outsider.id(), "[]", None).unwrap())
                .unwrap();
        assert_eq!(empty["receipt"]["imported"], 0);
        assert!(alice.export_for(&outsider.id(), "[\"work\"]").is_err());
        assert!(alice.export_for(&bob.id(), "[\"private\"]").is_err());
        let allowed: Vec<String> =
            serde_json::from_str(&alice.allowed_workspaces(&bob.id()).unwrap()).unwrap();
        assert_eq!(allowed, ["work"]);
        let exported = alice.export_for(&bob.id(), "[\"work\"]").unwrap();
        assert!(!exported.contains("private"));
        assert_eq!(
            alice.fingerprint("[\"work\"]").unwrap(),
            bob.fingerprint("[\"work\"]").unwrap()
        );
        let retained_without_self =
            Domain::new(&hex::encode([3; 32]), "Unenrolled", &exported, "[]").unwrap();
        assert!(
            retained_without_self
                .export_for(&alice.id(), "[\"work\"]")
                .is_err()
        );
        assert!(retained_without_self.operations("[\"work\"]").is_err());
    }

    #[test]
    fn member_can_carry_an_existing_members_grant_but_not_an_unrelated_root() {
        let mut alice = domain(1);
        let mut bob = domain(2);
        let mut carol = domain(3);
        create(&mut alice, "work");
        grant(&mut alice, &bob.id(), 4);
        enroll(&alice, &mut bob);
        grant(&mut alice, &carol.id(), 5);
        enroll(&alice, &mut carol);
        let operations = carol.export_for(&bob.id(), "[\"work\"]").unwrap();
        let batch: Value =
            serde_json::from_str(&bob.prepare_import(&carol.id(), &operations, None).unwrap())
                .unwrap();
        assert_eq!(batch["receipt"]["imported"], 1);
        assert!(!snapshot(&bob).workspaces[0].members.contains(&carol.id()));
        commit(&mut bob, &batch);
        assert!(snapshot(&bob).workspaces[0].members.contains(&carol.id()));
        create(&mut carol, "unrelated");
        let unrelated = carol.operations("[\"unrelated\"]").unwrap();
        let before = bob.snapshot().unwrap();
        assert!(bob.prepare_import(&carol.id(), &unrelated, None).is_err());
        assert_eq!(bob.snapshot().unwrap(), before);
        let duplicate: Value =
            serde_json::from_str(&bob.prepare_import(&carol.id(), &operations, None).unwrap())
                .unwrap();
        assert!(duplicate["batch_id"].is_null());
        assert_eq!(duplicate["receipt"]["imported"], 0);
        assert_eq!(duplicate["receipt"]["already_present"], 3);
    }

    #[test]
    fn bootstrap_requires_selected_scopes_and_both_memberships_without_partial_publication() {
        let mut alice = domain(1);
        let mut bob = domain(2);
        let outsider = domain(3);
        create(&mut alice, "work");
        let ungranted = alice.operations("[\"work\"]").unwrap();
        assert!(
            bob.prepare_bootstrap(&alice.id(), "[\"work\"]", &ungranted)
                .is_err()
        );
        grant(&mut alice, &bob.id(), 4);
        let history = alice.export_for(&bob.id(), "[\"work\"]").unwrap();
        assert!(
            bob.prepare_bootstrap(&outsider.id(), "[\"work\"]", &history)
                .is_err()
        );
        assert!(
            bob.prepare_bootstrap(&alice.id(), "[\"missing\"]", &history)
                .is_err()
        );
        assert!(
            bob.prepare_bootstrap(&alice.id(), "[\"work\",\"missing\"]", &history)
                .is_err()
        );
        assert_eq!(snapshot(&bob).operation_count, 0);
        let mut tampered: Vec<SignedOperation> = serde_json::from_str(&history).unwrap();
        tampered[0].operation.id = "tampered".into();
        assert!(
            bob.prepare_bootstrap(
                &alice.id(),
                "[\"work\"]",
                &serde_json::to_string(&tampered).unwrap()
            )
            .is_err()
        );
        assert_eq!(snapshot(&bob).operation_count, 0);
        enroll(&alice, &mut bob);
        assert_eq!(snapshot(&bob).operation_count, 2);
    }

    #[test]
    fn concurrent_edits_keep_both_heads_and_fingerprints_ignore_arrival_order() {
        let mut alice = domain(1);
        let mut bob = domain(2);
        create(&mut alice, "work");
        let created = prepare(
            &mut alice,
            json!({"type":"create_resource","operation_id":"note-created", "workspace_id":"work", "resource_id":"note", "title":"Note", "body":"Original"}),
        );
        commit(&mut alice, &created);
        grant(&mut alice, &bob.id(), 4);
        enroll(&alice, &mut bob);
        let edit = |id: &str, body: &str| {
            json!({"type":"edit_resource","operation_id":id,
            "workspace_id":"work","resource_id":"note","expected_heads":["note-created"],"title":"Note","body":body})
        };
        let edit_a = prepare(&mut alice, edit("edit-alice", "Alice words"));
        let edit_b = prepare(&mut bob, edit("edit-bob", "Bob words"));
        commit(&mut alice, &edit_a);
        commit(&mut bob, &edit_b);
        let batch: Value = serde_json::from_str(
            &alice
                .prepare_import(
                    &bob.id(),
                    &bob.export_for(&alice.id(), "[\"work\"]").unwrap(),
                    None,
                )
                .unwrap(),
        )
        .unwrap();
        commit(&mut alice, &batch);
        let mut reversed: Vec<SignedOperation> =
            serde_json::from_str(&alice.export_for(&bob.id(), "[\"work\"]").unwrap()).unwrap();
        reversed.reverse();
        let batch: Value = serde_json::from_str(
            &bob.prepare_import(
                &alice.id(),
                &serde_json::to_string(&reversed).unwrap(),
                None,
            )
            .unwrap(),
        )
        .unwrap();
        commit(&mut bob, &batch);
        assert_eq!(snapshot(&alice).resources, snapshot(&bob).resources);
        assert_eq!(snapshot(&alice).resources[0].heads.len(), 2);
        assert_eq!(
            alice.fingerprint("[\"work\"]").unwrap(),
            bob.fingerprint("[\"work\"]").unwrap()
        );
        let conflict = prepare(&mut alice, edit("stale-edit", "Must not overwrite"));
        assert!(conflict["batch_id"].is_null());
        assert_eq!(conflict["receipt"]["type"], "conflict");
        assert_eq!(snapshot(&alice).resources[0].heads.len(), 2);
    }

    #[test]
    fn contacts_and_scopes_are_validated_at_the_typed_boundary() {
        let mut alice = domain(1);
        let bob = domain(2);
        let address = EndpointAddr::new(SecretKey::from_bytes(&[2; 32]).public());
        let contact = json!({"name":"Synthetic peer","address":address});
        let record = alice.validate_contact(&contact.to_string()).unwrap();
        assert!(alice.validate_peer(&record).is_err());
        create(&mut alice, "work");
        grant(&mut alice, &bob.id(), 9);
        assert_eq!(alice.validate_peer(&record).unwrap(), record);
        let mut wrong: Value = serde_json::from_str(&record).unwrap();
        wrong["id"] = alice.id().into();
        assert!(alice.validate_peer(&wrong.to_string()).is_err());
        assert!(alice.export_for(&bob.id(), "[\"work\",\"work\"]").is_err());
        let too_many: Vec<_> = (0..33).map(|index| format!("workspace-{index}")).collect();
        assert!(alice.allowed_workspaces("invalid").is_err());
        assert!(
            alice
                .operations(&serde_json::to_string(&too_many).unwrap())
                .is_err()
        );
        assert!(
            alice
                .prepare_grant("bad-token", "[\"work\"]", &bob.id())
                .is_err()
        );
        let repeat: Value = serde_json::from_str(
            &alice
                .prepare_grant(&hex::encode([9; 32]), "[\"work\"]", &bob.id())
                .unwrap(),
        )
        .unwrap();
        assert!(repeat["batch_id"].is_null());
        assert!(repeat["operations"].as_array().unwrap().is_empty());
    }
}
