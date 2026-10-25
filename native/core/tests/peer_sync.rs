mod common;
use common::note_body;

use anyhow::Context;
use tempfile::tempdir;
use workspace_core::{Command, Host, NetworkMode};

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cumulative_grants_sync_across_more_than_one_scope_batch() -> anyhow::Result<()> {
    let left_dir = tempdir()?;
    let right_dir = tempdir()?;
    let left = Host::open(left_dir.path(), "Laptop".into(), NetworkMode::Loopback).await?;
    let right = Host::open(right_dir.path(), "Phone".into(), NetworkMode::Loopback).await?;
    let workspace_ids: Vec<_> = (0..33)
        .map(|index| format!("workspace-{index:02}"))
        .collect();
    for workspace_id in &workspace_ids {
        left.command(Command::CreateWorkspace {
            operation_id: format!("create-{workspace_id}"),
            workspace_id: workspace_id.clone(),
            title: workspace_id.clone(),
        })
        .await?;
    }
    for selection in workspace_ids.chunks(11) {
        right
            .join(left.create_invite(selection.to_vec(), 600).await?.ticket)
            .await
            .with_context(|| format!("pairing {} selected workspaces", selection.len()))?;
    }
    for host in [&left, &right] {
        let snapshot = host.snapshot().await?;
        assert_eq!(snapshot.data.workspaces.len(), 33);
        assert_eq!(snapshot.peers[0].workspace_ids, workspace_ids);
    }
    right.set_foreground(false).await?;
    left.command(Command::CreateResource {
        operation_id: "left-original-scope".into(),
        workspace_id: workspace_ids[0].clone(),
        resource_id: "laptop-note".into(),
        title: "Original scope".into(),
        body: "Laptop writing".into(),
    })
    .await?;
    right
        .command(Command::CreateResource {
            operation_id: "right-additional-scope".into(),
            workspace_id: workspace_ids[32].clone(),
            resource_id: "phone-note".into(),
            title: "Additional scope".into(),
            body: "Phone writing".into(),
        })
        .await?;
    right.set_foreground(true).await?;
    for host in [&right, &left] {
        let report = host.sync().await?;
        assert!(
            report
                .peers
                .iter()
                .all(|peer| matches!(peer.state, workspace_core::SyncState::Synced { .. })),
            "{report:?}"
        );
    }
    let left_snapshot = left.snapshot().await?;
    let right_snapshot = right.snapshot().await?;
    assert_eq!(left_snapshot.data.resources.len(), 2);
    assert_eq!(left_snapshot.data.resources, right_snapshot.data.resources);
    for snapshot in [left_snapshot, right_snapshot] {
        assert_eq!(snapshot.peers[0].workspace_ids, workspace_ids);
    }
    right.shutdown().await?;
    left.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn paired_hosts_keep_concurrent_writing_across_real_connections() -> anyhow::Result<()> {
    let left_dir = tempdir()?;
    let right_dir = tempdir()?;
    let left = Host::open(left_dir.path(), "L".repeat(120), NetworkMode::Loopback).await?;
    let right = Host::open(right_dir.path(), "Phone".into(), NetworkMode::Loopback).await?;
    left.command(Command::CreateWorkspace {
        operation_id: "create-launch".into(),
        workspace_id: "launch".into(),
        title: "Launch".into(),
    })
    .await?;
    left.command(Command::CreateResource {
        operation_id: "create-brief".into(),
        workspace_id: "launch".into(),
        resource_id: "brief".into(),
        title: "Brief".into(),
        body: "Original".into(),
    })
    .await?;
    let invitation = left.create_invite(vec!["launch".into()], 600).await?;
    right.join(invitation.ticket).await?;
    assert_eq!(
        note_body(&right.snapshot().await?.data.resources[0].heads[0]),
        "Original"
    );

    right.set_foreground(false).await?;
    left.command(Command::EditResource {
        operation_id: "laptop-edit".into(),
        workspace_id: "launch".into(),
        resource_id: "brief".into(),
        expected_heads: vec!["create-brief".into()],
        title: "Brief".into(),
        body: "Laptop writing".into(),
    })
    .await?;
    right
        .command(Command::EditResource {
            operation_id: "phone-edit".into(),
            workspace_id: "launch".into(),
            resource_id: "brief".into(),
            expected_heads: vec!["create-brief".into()],
            title: "Brief".into(),
            body: "Phone writing".into(),
        })
        .await?;
    right.set_foreground(true).await?;
    right.sync().await?;
    let merged = right.snapshot().await?;
    let heads = &merged.data.resources[0].heads;
    assert_eq!(heads.len(), 2, "Both offline proposals must survive");
    assert!(heads.iter().any(|head| note_body(head) == "Phone writing"));
    assert!(heads.iter().any(|head| note_body(head) == "Laptop writing"));
    assert_eq!(left.snapshot().await?.data.resources, merged.data.resources);
    let identity = right.snapshot().await?.data.device_id;
    right.shutdown().await?;
    drop(right);
    let reopened = Host::open(right_dir.path(), "Phone".into(), NetworkMode::Loopback).await?;
    assert_eq!(reopened.snapshot().await?.data.device_id, identity);
    assert_eq!(
        reopened.snapshot().await?.data.resources,
        merged.data.resources
    );
    reopened.shutdown().await?;
    left.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn new_member_reaches_an_existing_peer_after_inviter_stops() -> anyhow::Result<()> {
    let a_dir = tempdir()?;
    let b_dir = tempdir()?;
    let c_dir = tempdir()?;
    let a = Host::open(a_dir.path(), "Inviter".into(), NetworkMode::Loopback).await?;
    a.command(Command::CreateWorkspace {
        operation_id: "mesh-create".into(),
        workspace_id: "mesh".into(),
        title: "Shared".into(),
    })
    .await?;
    let b = Host::open(b_dir.path(), "Desktop".into(), NetworkMode::Loopback).await?;
    b.join(a.create_invite(vec!["mesh".into()], 600).await?.ticket)
        .await?;
    let c = Host::open(c_dir.path(), "Phone".into(), NetworkMode::Loopback).await?;
    c.join(a.create_invite(vec!["mesh".into()], 600).await?.ticket)
        .await?;
    a.set_foreground(false).await?;
    c.command(Command::CreateResource {
        operation_id: "phone-origin".into(),
        workspace_id: "mesh".into(),
        resource_id: "phone-note".into(),
        title: "Without inviter".into(),
        body: "Phone and desktop carry on".into(),
    })
    .await?;
    c.sync().await?;
    let b_state = b.snapshot().await?;
    assert!(b_state.data.resources.iter().any(|r| r.id == "phone-note"
        && note_body(&r.heads[0]) == "Phone and desktop carry on"));
    let c_id = c.snapshot().await?.data.device_id;
    assert!(b_state.peers.iter().any(|p| p.id == c_id));
    c.shutdown().await?;
    b.shutdown().await?;
    a.shutdown().await?;
    Ok(())
}
