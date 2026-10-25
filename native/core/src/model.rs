use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    CreateWorkspace {
        operation_id: String,
        workspace_id: String,
        title: String,
    },
    CreateResource {
        operation_id: String,
        workspace_id: String,
        resource_id: String,
        title: String,
        body: String,
    },
    EditResource {
        operation_id: String,
        workspace_id: String,
        resource_id: String,
        expected_heads: Vec<String>,
        title: String,
        body: String,
    },
    AddPlacement {
        operation_id: String,
        workspace_id: String,
        resource_id: String,
        source_workspace_id: String,
    },
    RemovePlacement {
        operation_id: String,
        workspace_id: String,
        resource_id: String,
        source_workspace_id: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum CommandReceipt {
    Rejected {
        operation_id: String,
        workspace_id: String,
        message: String,
    },
    Applied {
        operation_id: String,
        workspace_id: String,
        replayed: bool,
    },
    Conflict {
        resource_id: String,
        expected_heads: Vec<String>,
        actual_heads: Vec<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Revision {
    pub id: String,
    pub author: String,
    pub title: String,
    pub body: String,
    pub parents: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Placement {
    pub resource_id: String,
    pub source_workspace_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceSnapshot {
    pub id: String,
    pub title: String,
    pub placements: Vec<Placement>,
    pub members: Vec<String>,
    pub heads: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceSnapshot {
    pub id: String,
    pub workspace_id: String,
    pub heads: Vec<Revision>,
    pub history: Vec<Revision>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub device_id: String,
    pub device_name: String,
    pub workspaces: Vec<WorkspaceSnapshot>,
    pub resources: Vec<ResourceSnapshot>,
    pub operation_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    CreateWorkspace {
        title: String,
    },
    GrantMember {
        member_id: String,
    },
    CreateResource {
        resource_id: String,
        title: String,
        body: String,
    },
    EditResource {
        resource_id: String,
        expected_heads: Vec<String>,
        title: String,
        body: String,
    },
    AddPlacement {
        resource_id: String,
        source_workspace_id: String,
    },
    RemovePlacement {
        resource_id: String,
        source_workspace_id: String,
        observed_adds: Vec<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    pub id: String,
    pub workspace_id: String,
    pub author: String,
    pub parents: Vec<String>,
    pub action: Action,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignedOperation {
    pub version: u8,
    pub operation: Operation,
    pub signature: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PeerRecord {
    pub id: String,
    pub name: String,
    pub address_json: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportReceipt {
    pub imported: usize,
    pub already_present: usize,
}

use anyhow::{Context, Result, bail, ensure};
use iroh::{EndpointId, SecretKey, Signature};
use std::collections::{BTreeMap, BTreeSet};

pub const MAX_OPERATIONS: usize = 4096;
pub const MAX_DOCUMENT_BYTES: usize = 128 * 1024;
pub const MAX_HISTORY_BYTES: usize = 7 * 1024 * 1024;
const SIGNATURE_DOMAIN: &[u8] = b"workspace/operation/v1\0";

pub fn validate_id(value: &str) -> Result<()> {
    ensure!(
        !value.is_empty()
            && value.len() <= 128
            && value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'),
        "invalid identity"
    );
    Ok(())
}

pub fn validate_title(value: &str) -> Result<()> {
    ensure!(
        !value.trim().is_empty() && value.len() <= 512 && !value.chars().any(char::is_control),
        "invalid title"
    );
    Ok(())
}

pub fn validate_device(value: &str) -> Result<EndpointId> {
    let id: EndpointId = value.parse()?;
    ensure!(id.to_string() == value, "device identity is not canonical");
    Ok(id)
}

fn validate_body(value: &str) -> Result<()> {
    ensure!(
        value.len() <= MAX_DOCUMENT_BYTES && !value.contains('\0'),
        "invalid document body"
    );
    Ok(())
}

fn validate_list(values: &[String], maximum: usize) -> Result<()> {
    ensure!(values.len() <= maximum, "too many identities");
    let mut seen = BTreeSet::new();
    for value in values {
        validate_id(value)?;
        ensure!(seen.insert(value), "duplicate identity");
    }
    Ok(())
}

impl Command {
    pub fn identity(&self) -> (&str, &str) {
        match self {
            Self::CreateWorkspace {
                operation_id,
                workspace_id,
                ..
            }
            | Self::CreateResource {
                operation_id,
                workspace_id,
                ..
            }
            | Self::EditResource {
                operation_id,
                workspace_id,
                ..
            }
            | Self::AddPlacement {
                operation_id,
                workspace_id,
                ..
            }
            | Self::RemovePlacement {
                operation_id,
                workspace_id,
                ..
            } => (operation_id, workspace_id),
        }
    }
    pub fn validate(&self) -> Result<()> {
        let (id, workspace) = self.identity();
        validate_id(id)?;
        validate_id(workspace)?;
        match self {
            Self::CreateWorkspace { title, .. } => validate_title(title)?,
            Self::CreateResource {
                resource_id,
                title,
                body,
                ..
            } => {
                validate_id(resource_id)?;
                validate_title(title)?;
                validate_body(body)?;
            }
            Self::EditResource {
                resource_id,
                expected_heads,
                title,
                body,
                ..
            } => {
                validate_id(resource_id)?;
                validate_list(expected_heads, 64)?;
                ensure!(
                    !expected_heads.is_empty(),
                    "an edit requires a base revision"
                );
                validate_title(title)?;
                validate_body(body)?;
            }
            Self::AddPlacement {
                resource_id,
                source_workspace_id,
                ..
            }
            | Self::RemovePlacement {
                resource_id,
                source_workspace_id,
                ..
            } => {
                validate_id(resource_id)?;
                validate_id(source_workspace_id)?;
            }
        }
        Ok(())
    }
}

fn signing_bytes(operation: &Operation) -> Result<Vec<u8>> {
    let mut bytes = SIGNATURE_DOMAIN.to_vec();
    bytes.extend(serde_json::to_vec(operation)?);
    Ok(bytes)
}

impl SignedOperation {
    pub fn sign(operation: Operation, key: &SecretKey) -> Result<Self> {
        ensure!(
            operation.author == key.public().to_string(),
            "signer does not match author"
        );
        let signature = hex::encode(key.sign(&signing_bytes(&operation)?).to_bytes());
        Ok(Self {
            version: 1,
            operation,
            signature,
        })
    }
    pub fn verify(&self) -> Result<()> {
        ensure!(self.version == 1, "unsupported operation version");
        let op = &self.operation;
        validate_id(&op.id)?;
        validate_id(&op.workspace_id)?;
        validate_list(&op.parents, 64)?;
        let author = validate_device(&op.author)?;
        let signature_bytes: [u8; 64] = hex::decode(&self.signature)?
            .try_into()
            .map_err(|_| anyhow::anyhow!("invalid signature length"))?;
        ensure!(
            hex::encode(signature_bytes) == self.signature,
            "signature is not canonical"
        );
        author.verify(
            &signing_bytes(op)?,
            &Signature::from_bytes(&signature_bytes),
        )?;
        match &op.action {
            Action::CreateWorkspace { title } => validate_title(title)?,
            Action::GrantMember { member_id } => {
                validate_device(member_id)?;
            }
            Action::CreateResource {
                resource_id,
                title,
                body,
            } => {
                validate_id(resource_id)?;
                validate_title(title)?;
                validate_body(body)?;
            }
            Action::EditResource {
                resource_id,
                expected_heads,
                title,
                body,
            } => {
                validate_id(resource_id)?;
                validate_list(expected_heads, 64)?;
                ensure!(!expected_heads.is_empty(), "edit has no base");
                validate_title(title)?;
                validate_body(body)?;
            }
            Action::AddPlacement {
                resource_id,
                source_workspace_id,
            } => {
                validate_id(resource_id)?;
                validate_id(source_workspace_id)?;
            }
            Action::RemovePlacement {
                resource_id,
                source_workspace_id,
                observed_adds,
            } => {
                validate_id(resource_id)?;
                validate_id(source_workspace_id)?;
                validate_list(observed_adds, MAX_OPERATIONS)?;
            }
        }
        Ok(())
    }
}

#[derive(Default, Clone)]
pub(crate) struct Projection {
    pub workspaces: BTreeMap<String, WorkspaceSnapshot>,
    pub resources: BTreeMap<String, ResourceSnapshot>,
    pub placement_tags: BTreeMap<(String, Placement), BTreeSet<String>>,
}

fn revision_heads(revisions: impl Iterator<Item = Revision>) -> Vec<Revision> {
    let revisions: BTreeMap<_, _> = revisions.map(|r| (r.id.clone(), r)).collect();
    let parents: BTreeSet<_> = revisions.values().flat_map(|r| r.parents.iter()).collect();
    revisions
        .values()
        .filter(|r| !parents.contains(&r.id))
        .cloned()
        .collect()
}

/// Validate the signed causal history and derive order-independent views.
/// Neither arrival order nor a wall clock decides which concurrent text wins.
pub(crate) fn project(operations: &[SignedOperation]) -> Result<Projection> {
    ensure!(
        operations.len() <= MAX_OPERATIONS,
        "workspace history limit reached"
    );
    let mut pending = BTreeMap::new();
    let mut history_bytes = 0usize;
    for signed in operations {
        history_bytes = history_bytes
            .checked_add(serde_json::to_vec(signed)?.len())
            .ok_or_else(|| anyhow::anyhow!("history size overflow"))?;
        ensure!(
            history_bytes <= MAX_HISTORY_BYTES,
            "retained history size limit reached"
        );
        signed.verify()?;
        ensure!(
            pending
                .insert(signed.operation.id.clone(), signed)
                .is_none(),
            "duplicate operation identity"
        );
    }
    let mut done: BTreeMap<String, &SignedOperation> = BTreeMap::new();
    let mut ancestors: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut result = Projection::default();
    let mut roots: BTreeMap<String, String> = BTreeMap::new();
    while !pending.is_empty() {
        let ready = pending
            .iter()
            .find(|(_, s)| s.operation.parents.iter().all(|p| done.contains_key(p)))
            .map(|(id, _)| id.clone());
        let Some(id) = ready else {
            bail!("missing parent or cyclic operation history")
        };
        let signed = pending.remove(&id).unwrap();
        let op = &signed.operation;
        let mut past = BTreeSet::new();
        for parent in &op.parents {
            ensure!(
                done[parent].operation.workspace_id == op.workspace_id,
                "parent belongs to another workspace"
            );
            past.insert(parent.clone());
            past.extend(ancestors[parent].iter().cloned());
        }
        match &op.action {
            Action::CreateWorkspace { title } => {
                ensure!(
                    op.parents.is_empty() && !roots.contains_key(&op.workspace_id),
                    "workspace has conflicting origins"
                );
                roots.insert(op.workspace_id.clone(), id.clone());
                result.workspaces.insert(
                    op.workspace_id.clone(),
                    WorkspaceSnapshot {
                        id: op.workspace_id.clone(),
                        title: title.clone(),
                        placements: vec![],
                        members: vec![op.author.clone()],
                        heads: vec![],
                    },
                );
            }
            _ => {
                let root = roots
                    .get(&op.workspace_id)
                    .ok_or_else(|| anyhow::anyhow!("workspace origin is absent"))?;
                ensure!(
                    past.contains(root),
                    "operation does not descend from workspace origin"
                );
                let member = done[root].operation.author == op.author || past.iter().any(|p| matches!(&done[p].operation.action, Action::GrantMember { member_id } if member_id == &op.author));
                ensure!(
                    member,
                    "operation author is not an authorized workspace member"
                );
            }
        }
        match &op.action {
            Action::CreateWorkspace { .. } => {}
            Action::GrantMember { member_id } => {
                let members = &mut result.workspaces.get_mut(&op.workspace_id).unwrap().members;
                if !members.contains(member_id) {
                    members.push(member_id.clone());
                    members.sort();
                }
            }
            Action::CreateResource {
                resource_id,
                title,
                body,
            } => {
                ensure!(
                    !result.resources.contains_key(resource_id),
                    "resource has conflicting origins"
                );
                result.resources.insert(
                    resource_id.clone(),
                    ResourceSnapshot {
                        id: resource_id.clone(),
                        workspace_id: op.workspace_id.clone(),
                        heads: vec![],
                        history: vec![Revision {
                            id: id.clone(),
                            author: op.author.clone(),
                            title: title.clone(),
                            body: body.clone(),
                            parents: vec![],
                        }],
                    },
                );
                result
                    .placement_tags
                    .entry((
                        op.workspace_id.clone(),
                        Placement {
                            resource_id: resource_id.clone(),
                            source_workspace_id: op.workspace_id.clone(),
                        },
                    ))
                    .or_default()
                    .insert(id.clone());
            }
            Action::EditResource {
                resource_id,
                expected_heads,
                title,
                body,
            } => {
                let resource = result
                    .resources
                    .get_mut(resource_id)
                    .ok_or_else(|| anyhow::anyhow!("edited resource is absent"))?;
                ensure!(
                    resource.workspace_id == op.workspace_id,
                    "resource belongs to another workspace"
                );
                let known: Vec<Revision> = resource
                    .history
                    .iter()
                    .filter(|r| past.contains(&r.id))
                    .cloned()
                    .collect();
                let expected: BTreeSet<_> = expected_heads.iter().cloned().collect();
                let actual: BTreeSet<_> = revision_heads(known.into_iter())
                    .into_iter()
                    .map(|r| r.id)
                    .collect();
                ensure!(
                    !actual.is_empty() && expected == actual,
                    "edit does not name its causal resource heads"
                );
                resource.history.push(Revision {
                    id: id.clone(),
                    author: op.author.clone(),
                    title: title.clone(),
                    body: body.clone(),
                    parents: expected_heads.clone(),
                });
            }
            Action::AddPlacement {
                resource_id,
                source_workspace_id,
            } => {
                result
                    .placement_tags
                    .entry((
                        op.workspace_id.clone(),
                        Placement {
                            resource_id: resource_id.clone(),
                            source_workspace_id: source_workspace_id.clone(),
                        },
                    ))
                    .or_default()
                    .insert(id.clone());
            }
            Action::RemovePlacement {
                resource_id,
                source_workspace_id,
                observed_adds,
            } => {
                let placement = Placement {
                    resource_id: resource_id.clone(),
                    source_workspace_id: source_workspace_id.clone(),
                };
                for add in observed_adds {
                    ensure!(past.contains(add), "removal names an unobserved placement");
                    let matches = match &done[add].operation.action {
                        Action::CreateResource {
                            resource_id: origin,
                            ..
                        } => origin == resource_id && source_workspace_id == &op.workspace_id,
                        Action::AddPlacement {
                            resource_id: origin,
                            source_workspace_id: source,
                        } => origin == resource_id && source == source_workspace_id,
                        _ => false,
                    };
                    ensure!(matches, "removal names another placement");
                }
                let tags = result
                    .placement_tags
                    .entry((op.workspace_id.clone(), placement))
                    .or_default();
                for add in observed_adds {
                    tags.remove(add);
                }
            }
        }
        ancestors.insert(id.clone(), past);
        done.insert(id, signed);
    }
    for (_, placement) in result.placement_tags.keys() {
        if let Some(resource) = result.resources.get(&placement.resource_id) {
            ensure!(
                resource.workspace_id == placement.source_workspace_id,
                "placement names another resource origin"
            );
        }
    }
    for workspace in result.workspaces.values_mut() {
        let own: BTreeSet<_> = operations
            .iter()
            .filter(|s| s.operation.workspace_id == workspace.id)
            .map(|s| s.operation.id.clone())
            .collect();
        let parents: BTreeSet<_> = operations
            .iter()
            .filter(|s| s.operation.workspace_id == workspace.id)
            .flat_map(|s| s.operation.parents.iter().cloned())
            .collect();
        workspace.heads = own.difference(&parents).cloned().collect();
        workspace.placements = result
            .placement_tags
            .iter()
            .filter(|((id, _), tags)| id == &workspace.id && !tags.is_empty())
            .map(|((_, placement), _)| placement.clone())
            .collect();
    }
    for resource in result.resources.values_mut() {
        resource.history.sort_by(|a, b| a.id.cmp(&b.id));
        resource.heads = revision_heads(resource.history.iter().cloned());
    }
    Ok(result)
}

/// Pure preparation against one immutable cached observation. No transaction has begun.
pub(crate) fn prepare_command(
    command: &Command,
    actor: &SecretKey,
    existing: &[SignedOperation],
    projection: &Projection,
    requests: &BTreeMap<String, Command>,
) -> Result<PreparedCommand> {
    command.validate()?;
    let (id, workspace_id) = command.identity();
    let payload = serde_json::to_string(command)?;
    if let Some(saved) = requests.get(id) {
        ensure!(
            saved == command,
            "operation identity was already used with different content"
        );
        return Ok(PreparedCommand::Reply(CommandReceipt::Applied {
            operation_id: id.into(),
            workspace_id: workspace_id.into(),
            replayed: true,
        }));
    }
    let mut operations = existing.to_vec();
    ensure!(
        !operations.iter().any(|s| s.operation.id == id),
        "operation identity already exists"
    );
    let parents = match command {
        Command::CreateWorkspace { .. } => {
            ensure!(
                !projection.workspaces.contains_key(workspace_id),
                "workspace already exists"
            );
            vec![]
        }
        _ => {
            let workspace = projection
                .workspaces
                .get(workspace_id)
                .context("workspace is absent")?;
            ensure!(
                workspace.members.contains(&actor.public().to_string()),
                "this device is not a workspace member"
            );
            workspace.heads.clone()
        }
    };
    let action = match command {
        Command::CreateWorkspace { title, .. } => Action::CreateWorkspace {
            title: title.clone(),
        },
        Command::CreateResource {
            resource_id,
            title,
            body,
            ..
        } => Action::CreateResource {
            resource_id: resource_id.clone(),
            title: title.clone(),
            body: body.clone(),
        },
        Command::EditResource {
            resource_id,
            expected_heads,
            title,
            body,
            ..
        } => {
            let resource = projection
                .resources
                .get(resource_id)
                .context("resource is absent")?;
            ensure!(
                resource.workspace_id == workspace_id,
                "resource belongs to another workspace"
            );
            let actual: Vec<_> = resource.heads.iter().map(|r| r.id.clone()).collect();
            let mut expected = expected_heads.clone();
            expected.sort();
            if expected != actual {
                return Ok(PreparedCommand::Reply(CommandReceipt::Conflict {
                    resource_id: resource_id.clone(),
                    expected_heads: expected_heads.clone(),
                    actual_heads: actual,
                }));
            }
            Action::EditResource {
                resource_id: resource_id.clone(),
                expected_heads: expected,
                title: title.clone(),
                body: body.clone(),
            }
        }
        Command::AddPlacement {
            resource_id,
            source_workspace_id,
            ..
        } => Action::AddPlacement {
            resource_id: resource_id.clone(),
            source_workspace_id: source_workspace_id.clone(),
        },
        Command::RemovePlacement {
            resource_id,
            source_workspace_id,
            ..
        } => {
            let key = (
                workspace_id.to_string(),
                Placement {
                    resource_id: resource_id.clone(),
                    source_workspace_id: source_workspace_id.clone(),
                },
            );
            let observed_adds = projection
                .placement_tags
                .get(&key)
                .map(|tags| tags.iter().cloned().collect())
                .unwrap_or_default();
            Action::RemovePlacement {
                resource_id: resource_id.clone(),
                source_workspace_id: source_workspace_id.clone(),
                observed_adds,
            }
        }
    };
    let signed = SignedOperation::sign(
        Operation {
            id: id.into(),
            workspace_id: workspace_id.into(),
            author: actor.public().to_string(),
            parents,
            action,
        },
        actor,
    )?;
    operations.push(signed.clone());
    let projection = project(&operations)?;
    Ok(PreparedCommand::Write(Box::new(PreparedWrite {
        signed,
        operations,
        projection,
        request_payload: payload,
    })))
}

pub(crate) enum PreparedCommand {
    Reply(CommandReceipt),
    Write(Box<PreparedWrite>),
}
pub(crate) struct PreparedWrite {
    pub signed: SignedOperation,
    pub operations: Vec<SignedOperation>,
    pub projection: Projection,
    pub request_payload: String,
}
