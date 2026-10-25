use workspace_core::{
    model::{Command, CommandReceipt},
    store::Store,
};

#[tokio::test]
async fn device_names_have_the_same_byte_limit_for_local_and_saved_peers() {
    let directory = tempfile::tempdir().unwrap();
    let maximum_name = "x".repeat(120);
    let oversized_name = "x".repeat(121);
    assert!(
        Store::open(directory.path(), &oversized_name)
            .await
            .is_err()
    );
    let store = Store::open(directory.path(), &maximum_name).await.unwrap();
    assert_eq!(store.snapshot().await.unwrap().device_name, maximum_name);
    create_launch(&store).await;
    store.save_peer(peer(&store, &maximum_name)).await.unwrap();
    assert!(
        store
            .save_peer(peer(&store, &oversized_name))
            .await
            .is_err()
    );
    assert_eq!(store.peers().await.unwrap()[0].name, maximum_name);
    for invalid in [" ", "Phone\n", &"é".repeat(61)] {
        let invalid_directory = tempfile::tempdir().unwrap();
        assert!(
            Store::open(invalid_directory.path(), invalid)
                .await
                .is_err()
        );
        assert!(store.save_peer(peer(&store, invalid)).await.is_err());
    }
    assert_eq!(store.peers().await.unwrap()[0].name, maximum_name);
}

#[tokio::test]
async fn saved_workspace_and_note_survive_reopening_with_the_same_identity() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "Laptop").await.unwrap();
    let initial = store.snapshot().await.unwrap();
    let workspace = Command::CreateWorkspace {
        operation_id: "create-launch".into(),
        workspace_id: "launch".into(),
        title: "Launch review".into(),
    };
    assert!(matches!(
        store.command(workspace.clone()).await.unwrap(),
        CommandReceipt::Applied {
            replayed: false,
            ..
        }
    ));
    store
        .command(Command::CreateResource {
            operation_id: "create-brief".into(),
            workspace_id: "launch".into(),
            resource_id: "brief".into(),
            title: "Pilot decision".into(),
            body: "Start with three teams.".into(),
        })
        .await
        .unwrap();
    drop(store);
    let reopened = Store::open(directory.path(), "Renamed laptop")
        .await
        .unwrap();
    let snapshot = reopened.snapshot().await.unwrap();
    assert_eq!(snapshot.device_id, initial.device_id);
    assert_eq!(snapshot.workspaces[0].title, "Launch review");
    assert_eq!(
        snapshot.resources[0].heads[0].body,
        "Start with three teams."
    );
    assert!(matches!(
        reopened.command(workspace).await.unwrap(),
        CommandReceipt::Applied { replayed: true, .. }
    ));
    assert_eq!(reopened.snapshot().await.unwrap().operation_count, 2);
}

fn peer(store: &Store, name: &str) -> workspace_core::model::PeerRecord {
    workspace_core::model::PeerRecord {
        id: store.device_id(),
        name: name.into(),
        address_json: serde_json::to_string(&iroh::EndpointAddr::new(store.secret_key().public()))
            .unwrap(),
    }
}

async fn create_launch(store: &Store) {
    store
        .command(Command::CreateWorkspace {
            operation_id: "origin".into(),
            workspace_id: "launch".into(),
            title: "Launch".into(),
        })
        .await
        .unwrap();
    store
        .command(Command::CreateResource {
            operation_id: "first-draft".into(),
            workspace_id: "launch".into(),
            resource_id: "draft".into(),
            title: "Follow-up".into(),
            body: "Original".into(),
        })
        .await
        .unwrap();
}

async fn enroll(inviter: &Store, recipient: &Store, token: &str) {
    let workspaces = vec!["launch".to_string()];
    inviter
        .create_invite(token.into(), workspaces.clone(), 1000)
        .await
        .unwrap();
    assert_eq!(
        inviter
            .accept_invite(token, peer(recipient, "Other device"), 10)
            .await
            .unwrap(),
        workspaces
    );
    recipient
        .bootstrap(
            peer(inviter, "Inviter"),
            workspaces.clone(),
            inviter.operations(&workspaces).await.unwrap(),
        )
        .await
        .unwrap();
}

#[tokio::test]
async fn new_member_can_carry_a_signed_grant_to_an_existing_peer_without_the_inviter() {
    let da = tempfile::tempdir().unwrap();
    let db = tempfile::tempdir().unwrap();
    let dc = tempfile::tempdir().unwrap();
    let a = Store::open(da.path(), "A").await.unwrap();
    let b = Store::open(db.path(), "B").await.unwrap();
    let c = Store::open(dc.path(), "C").await.unwrap();
    create_launch(&a).await;
    enroll(&a, &b, &"11".repeat(32)).await;
    enroll(&a, &c, &"22".repeat(32)).await;
    drop(a);
    c.command(Command::EditResource {
        operation_id: "phone-edit".into(),
        workspace_id: "launch".into(),
        resource_id: "draft".into(),
        expected_heads: vec!["first-draft".into()],
        title: "Follow-up".into(),
        body: "Written while inviter is offline".into(),
    })
    .await
    .unwrap();
    b.import(
        &c.device_id(),
        &c.operations(&["launch".into()]).await.unwrap(),
    )
    .await
    .unwrap();
    assert_eq!(
        b.snapshot().await.unwrap().resources[0].heads[0].body,
        "Written while inviter is offline"
    );
    assert_eq!(
        b.allowed_workspaces(&c.device_id()).await.unwrap(),
        vec!["launch"]
    );
}

#[tokio::test]
async fn offline_branches_are_preserved_and_resolution_rechecks_current_heads() {
    let da = tempfile::tempdir().unwrap();
    let db = tempfile::tempdir().unwrap();
    let a = Store::open(da.path(), "Laptop").await.unwrap();
    let b = Store::open(db.path(), "Phone").await.unwrap();
    create_launch(&a).await;
    enroll(&a, &b, &"33".repeat(32)).await;
    let edit = |id: &str, body: &str, heads: Vec<String>| Command::EditResource {
        operation_id: id.into(),
        workspace_id: "launch".into(),
        resource_id: "draft".into(),
        expected_heads: heads,
        title: "Follow-up".into(),
        body: body.into(),
    };
    a.command(edit(
        "laptop-edit",
        "Laptop wording",
        vec!["first-draft".into()],
    ))
    .await
    .unwrap();
    b.command(edit(
        "phone-edit",
        "Phone wording",
        vec!["first-draft".into()],
    ))
    .await
    .unwrap();
    let workspace = vec!["launch".into()];
    a.import(&b.device_id(), &b.operations(&workspace).await.unwrap())
        .await
        .unwrap();
    b.import(&a.device_id(), &a.operations(&workspace).await.unwrap())
        .await
        .unwrap();
    let snapshot = a.snapshot().await.unwrap();
    assert_eq!(
        snapshot.resources[0]
            .heads
            .iter()
            .map(|r| r.body.as_str())
            .collect::<Vec<_>>(),
        vec!["Laptop wording", "Phone wording"]
    );
    assert_eq!(snapshot.resources, b.snapshot().await.unwrap().resources);
    assert!(
        matches!(
            a.command(edit(
                "stale-resolution",
                "Would lose phone text",
                vec!["laptop-edit".into()]
            ))
            .await
            .unwrap(),
            CommandReceipt::Conflict { .. }
        ),
        "reject-stale-heads"
    );
    a.command(edit(
        "resolved",
        "Both choices reviewed",
        vec!["laptop-edit".into(), "phone-edit".into()],
    ))
    .await
    .unwrap();
    let resolved = a.snapshot().await.unwrap();
    assert_eq!(resolved.resources[0].heads.len(), 1);
    assert_eq!(resolved.resources[0].heads[0].body, "Both choices reviewed");
    assert_eq!(resolved.resources[0].history.len(), 4);
    b.import(&a.device_id(), &a.operations(&workspace).await.unwrap())
        .await
        .unwrap();
    assert_eq!(resolved.resources, b.snapshot().await.unwrap().resources);
}

#[tokio::test]
async fn invalid_signature_unknown_author_and_changed_operation_reuse_are_denied_atomically() {
    use workspace_core::model::{Action, Operation, SignedOperation};
    let da = tempfile::tempdir().unwrap();
    let db = tempfile::tempdir().unwrap();
    let a = Store::open(da.path(), "Laptop").await.unwrap();
    let b = Store::open(db.path(), "Phone").await.unwrap();
    create_launch(&a).await;
    enroll(&a, &b, &"44".repeat(32)).await;
    let before = a.snapshot().await.unwrap();
    let workspace = vec!["launch".into()];
    b.command(Command::CreateResource {
        operation_id: "signed-note".into(),
        workspace_id: "launch".into(),
        resource_id: "signed-resource".into(),
        title: "Legitimate note".into(),
        body: "Signed original".into(),
    })
    .await
    .unwrap();
    let mut forged = b
        .operations(&workspace)
        .await
        .unwrap()
        .into_iter()
        .find(|s| s.operation.id == "signed-note")
        .unwrap();
    if let Action::CreateResource { body, .. } = &mut forged.operation.action {
        *body = "Forged content".into();
    }
    assert!(
        a.import(&b.device_id(), &[forged]).await.is_err(),
        "reject-invalid-signature"
    );
    let outsider = iroh::SecretKey::generate();
    let unknown = SignedOperation::sign(
        Operation {
            id: "unknown-author".into(),
            workspace_id: "launch".into(),
            author: outsider.public().to_string(),
            parents: before.workspaces[0].heads.clone(),
            action: Action::CreateResource {
                resource_id: "injected".into(),
                title: "Injection".into(),
                body: "Unauthorized writer".into(),
            },
        },
        &outsider,
    )
    .unwrap();
    assert!(
        a.import(&b.device_id(), &[unknown]).await.is_err(),
        "reject-ungranted-author"
    );
    let mut reused = b
        .operations(&workspace)
        .await
        .unwrap()
        .into_iter()
        .find(|s| s.operation.id == "first-draft")
        .unwrap()
        .operation;
    reused.author = b.device_id();
    let changed = SignedOperation::sign(reused, &b.secret_key()).unwrap();
    assert!(
        a.import(&b.device_id(), &[changed]).await.is_err(),
        "reject-operation-id-payload-reuse"
    );
    assert_eq!(before, a.snapshot().await.unwrap());
    let changed_command = Command::CreateWorkspace {
        operation_id: "origin".into(),
        workspace_id: "launch".into(),
        title: "Changed title".into(),
    };
    assert!(matches!(
        a.command(changed_command).await.unwrap(),
        CommandReceipt::Rejected { .. }
    ));
    assert_eq!(before, a.snapshot().await.unwrap());
}

#[tokio::test]
async fn invitation_replay_is_bound_to_the_accepted_peer_and_survives_expiry() {
    let da = tempfile::tempdir().unwrap();
    let db = tempfile::tempdir().unwrap();
    let dc = tempfile::tempdir().unwrap();
    let a = Store::open(da.path(), "Laptop").await.unwrap();
    let b = Store::open(db.path(), "Phone").await.unwrap();
    let c = Store::open(dc.path(), "Other").await.unwrap();
    create_launch(&a).await;
    let token = "55".repeat(32);
    a.create_invite(token.clone(), vec!["launch".into()], 100)
        .await
        .unwrap();
    assert!(
        a.accept_invite(&token, peer(&c, "Other"), 100)
            .await
            .is_err(),
        "unused expired invitation must fail"
    );
    a.accept_invite(&token, peer(&b, "Phone"), 99)
        .await
        .unwrap();
    let count = a.snapshot().await.unwrap().operation_count;
    assert_eq!(
        a.accept_invite(&token, peer(&b, "Phone"), 200)
            .await
            .unwrap(),
        vec!["launch"]
    );
    assert_eq!(a.snapshot().await.unwrap().operation_count, count);
    assert!(
        a.accept_invite(&token, peer(&c, "Other"), 99)
            .await
            .is_err()
    );
}

#[tokio::test]
async fn a_cross_workspace_reference_does_not_export_its_protected_material() {
    let da = tempfile::tempdir().unwrap();
    let db = tempfile::tempdir().unwrap();
    let a = Store::open(da.path(), "Laptop").await.unwrap();
    let b = Store::open(db.path(), "Phone").await.unwrap();
    create_launch(&a).await;
    a.command(Command::CreateWorkspace {
        operation_id: "personal-origin".into(),
        workspace_id: "personal".into(),
        title: "Personal".into(),
    })
    .await
    .unwrap();
    a.command(Command::AddPlacement {
        operation_id: "reference".into(),
        workspace_id: "personal".into(),
        resource_id: "draft".into(),
        source_workspace_id: "launch".into(),
    })
    .await
    .unwrap();
    let token = "66".repeat(32);
    let selected = vec!["personal".into()];
    a.create_invite(token.clone(), selected.clone(), 100)
        .await
        .unwrap();
    a.accept_invite(&token, peer(&b, "Phone"), 1).await.unwrap();
    b.bootstrap(
        peer(&a, "Laptop"),
        selected.clone(),
        a.operations(&selected).await.unwrap(),
    )
    .await
    .unwrap();
    let snapshot = b.snapshot().await.unwrap();
    assert_eq!(snapshot.workspaces.len(), 1);
    assert_eq!(snapshot.workspaces[0].placements[0].resource_id, "draft");
    assert!(
        snapshot.resources.is_empty(),
        "reference must reveal no protected title or content"
    );
    assert!(
        b.import(
            &a.device_id(),
            &a.operations(&["launch".into()]).await.unwrap()
        )
        .await
        .is_err()
    );
}

#[tokio::test]
async fn dropping_a_submitted_command_caller_does_not_cancel_the_durable_owner() {
    use std::{
        future::{Future, poll_fn},
        task::Poll,
        time::Duration,
    };
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "Laptop").await.unwrap();
    let command = Command::CreateWorkspace {
        operation_id: "detached-save".into(),
        workspace_id: "work".into(),
        title: "Work".into(),
    };
    let mut submitted = Box::pin(store.command(command.clone()));
    // Poll the public call so it reaches its owner, then discard only the caller.
    poll_fn(|cx| {
        let _ = submitted.as_mut().poll(cx);
        Poll::Ready(())
    })
    .await;
    drop(submitted);
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if store.snapshot().await.unwrap().operation_count == 1 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    assert!(matches!(
        store.command(command).await.unwrap(),
        CommandReceipt::Applied { replayed: true, .. }
    ));
}

#[tokio::test]
async fn definitive_validation_rejection_does_not_commit_and_allows_correction() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "Laptop").await.unwrap();
    let rejected = store
        .command(Command::CreateWorkspace {
            operation_id: "try-create".into(),
            workspace_id: "work".into(),
            title: "   ".into(),
        })
        .await
        .unwrap();
    assert!(
        matches!(rejected, CommandReceipt::Rejected { operation_id, workspace_id, .. } if operation_id == "try-create" && workspace_id == "work"),
        "reject-invalid-title-without-uncertainty"
    );
    assert_eq!(store.snapshot().await.unwrap().operation_count, 0);
    let absent = store
        .command(Command::CreateResource {
            operation_id: "try-note".into(),
            workspace_id: "work".into(),
            resource_id: "note".into(),
            title: "Note".into(),
            body: "Retained text".into(),
        })
        .await
        .unwrap();
    assert!(
        matches!(absent, CommandReceipt::Rejected { .. }),
        "reject-absent-workspace-without-uncertainty"
    );
    store
        .command(Command::CreateWorkspace {
            operation_id: "try-create".into(),
            workspace_id: "work".into(),
            title: "Corrected title".into(),
        })
        .await
        .unwrap();
    assert!(matches!(
        store
            .command(Command::CreateResource {
                operation_id: "try-note".into(),
                workspace_id: "work".into(),
                resource_id: "note".into(),
                title: "Note".into(),
                body: "Retained text".into()
            })
            .await
            .unwrap(),
        CommandReceipt::Applied {
            replayed: false,
            ..
        }
    ));
    assert_eq!(store.snapshot().await.unwrap().operation_count, 2);
}
