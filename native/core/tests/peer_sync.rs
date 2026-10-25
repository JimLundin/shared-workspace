use tempfile::tempdir;
use workspace_core::{Command, Host, NetworkMode};

#[tokio::test]
async fn paired_hosts_keep_concurrent_writing_across_real_connections() -> anyhow::Result<()> {
    let left_dir = tempdir()?;
    let right_dir = tempdir()?;
    let left = Host::open(left_dir.path(), "Laptop".into(), NetworkMode::Loopback).await?;
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
        right.snapshot().await?.data.resources[0].heads[0].body,
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
    assert!(heads.iter().any(|head| head.body == "Phone writing"));
    assert!(heads.iter().any(|head| head.body == "Laptop writing"));
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
    assert!(
        b_state
            .data
            .resources
            .iter()
            .any(|r| r.id == "phone-note" && r.heads[0].body == "Phone and desktop carry on")
    );
    let c_id = c.snapshot().await?.data.device_id;
    assert!(b_state.peers.iter().any(|p| p.id == c_id));
    c.shutdown().await?;
    b.shutdown().await?;
    a.shutdown().await?;
    Ok(())
}
