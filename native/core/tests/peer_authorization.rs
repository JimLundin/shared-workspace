//! Public-wire acceptance tests. Every exchange crosses a real Iroh connection.

use anyhow::{Context, Result, ensure};
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use iroh::endpoint::presets;
use iroh::{Endpoint, EndpointAddr, RelayMode, SecretKey};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::time::timeout;
use workspace_core::model::{Command, PeerRecord, SignedOperation};
use workspace_core::store::Store;
use workspace_core::{Host, NetworkMode};

const ALPN: &[u8] = b"workspace/replication/1";

struct WirePeer {
    endpoint: Endpoint,
    store: Store,
    target: EndpointAddr,
}

impl WirePeer {
    async fn pair(host: &Host, directory: &std::path::Path, workspace_id: &str) -> Result<Self> {
        let store = Store::open(directory, "Wire peer").await?;
        let endpoint = Endpoint::builder(presets::Minimal)
            .secret_key(store.secret_key())
            .relay_mode(RelayMode::Disabled)
            .clear_address_lookup()
            .clear_ip_transports()
            .bind_addr("127.0.0.1:0")?
            .alpns(vec![ALPN.to_vec()])
            .bind()
            .await?;
        let invite = host.create_invite(vec![workspace_id.into()], 600).await?;
        let encoded = invite
            .ticket
            .strip_prefix("workspace1:")
            .context("ticket version")?;
        let ticket: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(encoded)?)?;
        let target: EndpointAddr = serde_json::from_value(ticket["inviter"]["address"].clone())?;
        let peer = Self {
            endpoint,
            store,
            target,
        };
        let response = peer
            .exchange(json!({"type":"pair", "nonce":ticket["nonce"]}))
            .await?;
        ensure!(response["body"]["type"] == "paired", "pairing failed");
        let operations: Vec<SignedOperation> =
            serde_json::from_value(response["body"]["operations"].clone())?;
        let workspaces: Vec<String> =
            serde_json::from_value(response["body"]["workspace_ids"].clone())?;
        let address: EndpointAddr = serde_json::from_value(response["contact"]["address"].clone())?;
        peer.store
            .bootstrap(
                PeerRecord {
                    id: address.id.to_string(),
                    name: response["contact"]["name"]
                        .as_str()
                        .context("peer name")?
                        .into(),
                    address_json: serde_json::to_string(&address)?,
                },
                workspaces,
                operations,
            )
            .await?;
        Ok(peer)
    }

    async fn exchange(&self, body: Value) -> Result<Value> {
        let mut address = self.endpoint.addr();
        for socket in self.endpoint.bound_sockets() {
            address = address.with_ip_addr(socket);
        }
        let id = hex::encode(SecretKey::generate().to_bytes());
        let request = json!({
            "version":1, "id":id,
            "contact":{"name":"Wire peer", "address":address},
            "body":body,
        });
        timeout(Duration::from_secs(30), async {
            let connection = self.endpoint.connect(self.target.clone(), ALPN).await?;
            ensure!(
                connection.remote_id() == self.target.id,
                "authenticated identity differs"
            );
            let (mut send, mut receive) = connection.open_bi().await?;
            send.write_all(&serde_json::to_vec(&request)?).await?;
            send.finish()?;
            let response: Value =
                serde_json::from_slice(&receive.read_to_end(8 * 1024 * 1024).await?)?;
            ensure!(
                response["version"] == 1 && response["id"] == id,
                "response correlation differs"
            );
            connection.close(0u32.into(), b"test exchange complete");
            Ok(response)
        })
        .await
        .context("actual Iroh exchange did not complete")?
    }
}

async fn create_workspace(host: &Host, workspace_id: &str) -> Result<()> {
    host.command(Command::CreateWorkspace {
        operation_id: format!("create-{workspace_id}"),
        workspace_id: workspace_id.into(),
        title: workspace_id.into(),
    })
    .await?;
    Ok(())
}

#[tokio::test]
async fn a_paired_peer_cannot_export_an_ungranted_workspace_with_an_empty_history() -> Result<()> {
    let target_directory = tempfile::tempdir()?;
    let client_directory = tempfile::tempdir()?;
    let host = Host::open(
        target_directory.path(),
        "Target".into(),
        NetworkMode::Loopback,
    )
    .await?;
    create_workspace(&host, "shared").await?;
    create_workspace(&host, "private").await?;
    host.command(Command::CreateResource {
        operation_id: "private-note".into(),
        workspace_id: "private".into(),
        resource_id: "private-resource".into(),
        title: "Private note".into(),
        body: "Content not granted to the paired peer".into(),
    })
    .await?;
    let peer = WirePeer::pair(&host, client_directory.path(), "shared").await?;
    let before = host.snapshot().await?.data;
    let summary = peer
        .exchange(json!({
            "type":"summary", "workspace_ids":["private"],
            "fingerprint":"00".repeat(32), "contacts":[],
        }))
        .await?;
    assert_eq!(
        summary["body"]["type"], "rejected",
        "reject-ungranted-summary-scope"
    );
    let transfer = peer
        .exchange(json!({
            "type":"sync", "workspace_ids":["private"], "operations":[], "contacts":[],
        }))
        .await?;
    assert_eq!(
        transfer["body"]["type"], "rejected",
        "reject-ungranted-requested-scope"
    );
    assert_eq!(
        before,
        host.snapshot().await?.data,
        "rejected export must not change local work"
    );
    assert!(
        peer.store.snapshot().await?.resources.is_empty(),
        "private resource must not enter the client store"
    );
    peer.endpoint.close().await;
    host.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn matching_peer_summaries_omit_history_and_changed_work_requires_a_transfer() -> Result<()> {
    let target_directory = tempfile::tempdir()?;
    let client_directory = tempfile::tempdir()?;
    let host = Host::open(
        target_directory.path(),
        "Target".into(),
        NetworkMode::Loopback,
    )
    .await?;
    create_workspace(&host, "shared").await?;
    let peer = WirePeer::pair(&host, client_directory.path(), "shared").await?;
    let workspaces = vec!["shared".to_string()];
    let fingerprint = peer.store.fingerprint(&workspaces).await?;
    let summary = || json!({"type":"summary", "workspace_ids":workspaces, "fingerprint":fingerprint, "contacts":[]});
    let current = peer.exchange(summary()).await?;
    assert_eq!(
        current["body"]["type"], "current",
        "unchanged-workspace-summary-is-current"
    );
    assert!(
        current["body"].get("operations").is_none(),
        "matching-summary-must-not-return-history"
    );
    host.command(Command::CreateResource {
        operation_id: "new-note".into(),
        workspace_id: "shared".into(),
        resource_id: "new-resource".into(),
        title: "New note".into(),
        body: "A new retained revision".into(),
    })
    .await?;
    let changed = peer.exchange(summary()).await?;
    assert_eq!(
        changed["body"]["type"], "need_sync",
        "new-operation-changes-summary"
    );
    assert!(
        changed["body"].get("operations").is_none(),
        "summary-disagreement-must-not-embed-history"
    );
    let result = peer
        .exchange(json!({
            "type":"sync", "workspace_ids":workspaces,
            "operations":peer.store.operations(&workspaces).await?, "contacts":[],
        }))
        .await?;
    assert_eq!(result["body"]["type"], "synced");
    let operations: Vec<SignedOperation> =
        serde_json::from_value(result["body"]["operations"].clone())?;
    peer.store
        .import(&host.snapshot().await?.data.device_id, &operations)
        .await?;
    assert_eq!(
        peer.store.snapshot().await?.resources[0].heads[0].body,
        "A new retained revision"
    );
    let current = peer
        .exchange(json!({
            "type":"summary", "workspace_ids":workspaces,
            "fingerprint":peer.store.fingerprint(&workspaces).await?, "contacts":[],
        }))
        .await?;
    assert_eq!(current["body"]["type"], "current");
    assert!(current["body"].get("operations").is_none());
    peer.endpoint.close().await;
    host.shutdown().await?;
    Ok(())
}
