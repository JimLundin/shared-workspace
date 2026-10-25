mod common;
use common::note_body;

use iroh::SecretKey;
use workspace_core::{
    model::{
        Action, CodeLanguage, Command, CommandReceipt, Operation, PeerRecord, Revision,
        SignedOperation, Surface, WebApplication,
    },
    store::Store,
};

fn code(text: &str) -> Surface {
    Surface::CodeDocument {
        language: CodeLanguage::Python,
        text: text.into(),
    }
}

fn contact(key: &SecretKey, name: &str) -> PeerRecord {
    PeerRecord {
        id: key.public().to_string(),
        name: name.into(),
        address_json: serde_json::to_string(&iroh::EndpointAddr::new(key.public())).unwrap(),
    }
}

async fn applied(store: &Store, command: Command) {
    assert!(matches!(
        store.command(command).await.unwrap(),
        CommandReceipt::Applied {
            replayed: false,
            ..
        }
    ));
}

fn create(id: &str, surface: Surface) -> Command {
    Command::CreateSurface {
        operation_id: format!("create-{id}"),
        workspace_id: "work".into(),
        resource_id: id.into(),
        title: id.into(),
        surface,
    }
}

fn edit(id: &str, resource: &str, parents: &[&str], surface: Surface) -> Command {
    Command::EditSurface {
        operation_id: id.into(),
        workspace_id: "work".into(),
        resource_id: resource.into(),
        expected_heads: parents.iter().map(|p| (*p).into()).collect(),
        title: resource.into(),
        surface,
    }
}

async fn origin(store: &Store) {
    applied(
        store,
        Command::CreateWorkspace {
            operation_id: "root".into(),
            workspace_id: "work".into(),
            title: "Work".into(),
        },
    )
    .await;
}

async fn enroll(inviter: &Store, receiver: &Store) {
    let nonce = "42".repeat(32);
    inviter
        .create_invite(nonce.clone(), vec!["work".into()], 1000)
        .await
        .unwrap();
    inviter
        .accept_invite(&nonce, contact(&receiver.secret_key(), "Receiver"), 10)
        .await
        .unwrap();
    receiver
        .bootstrap(
            contact(&inviter.secret_key(), "Inviter"),
            vec!["work".into()],
            inviter.operations(&["work".into()]).await.unwrap(),
        )
        .await
        .unwrap();
}

#[tokio::test]
async fn application_document_and_code_surfaces_reopen_and_replay_exactly() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "Local").await.unwrap();
    origin(&store).await;
    let commands = [
        create(
            "word",
            Surface::Application {
                application: WebApplication::Word,
                url: "https://word.cloud.microsoft/".into(),
            },
        ),
        create(
            "document",
            Surface::Document {
                application: WebApplication::Website,
                url: "https://example.org/documents/brief".into(),
            },
        ),
        create("script", code("print('first')\n")),
    ];
    for command in &commands {
        applied(&store, command.clone()).await;
    }
    let revision = edit(
        "edit-script",
        "script",
        &["create-script"],
        code("print('saved')\n"),
    );
    applied(&store, revision.clone()).await;
    let before = store.snapshot().await.unwrap();
    assert_eq!(before.workspaces[0].placements.len(), 3);
    assert!(
        before
            .resources
            .iter()
            .all(|resource| matches!(resource.heads[0], Revision::Surface(_)))
    );
    drop(store);
    let reopened = Store::open(directory.path(), "Local").await.unwrap();
    assert_eq!(reopened.snapshot().await.unwrap(), before);
    for command in commands.into_iter().chain([revision]) {
        assert!(matches!(
            reopened.command(command).await.unwrap(),
            CommandReceipt::Applied { replayed: true, .. }
        ));
    }
    assert!(matches!(
        reopened
            .command(edit(
                "edit-script",
                "script",
                &["create-script"],
                code("different")
            ))
            .await
            .unwrap(),
        CommandReceipt::Rejected { .. }
    ));
    applied(
        &reopened,
        Command::RemovePlacement {
            operation_id: "remove-word".into(),
            workspace_id: "work".into(),
            resource_id: "word".into(),
            source_workspace_id: "work".into(),
        },
    )
    .await;
    let after = reopened.snapshot().await.unwrap();
    assert_eq!(after.workspaces[0].placements.len(), 2);
    assert_eq!(after.resources, before.resources);
}

#[tokio::test]
async fn concurrent_code_and_note_edits_preserve_branches_and_recheck_resolution() {
    let left_dir = tempfile::tempdir().unwrap();
    let right_dir = tempfile::tempdir().unwrap();
    let left = Store::open(left_dir.path(), "Left").await.unwrap();
    let right = Store::open(right_dir.path(), "Right").await.unwrap();
    origin(&left).await;
    applied(&left, create("script", code("original"))).await;
    applied(
        &left,
        Command::CreateResource {
            operation_id: "create-note".into(),
            workspace_id: "work".into(),
            resource_id: "note".into(),
            title: "Note".into(),
            body: "Original note".into(),
        },
    )
    .await;
    enroll(&left, &right).await;
    applied(
        &left,
        edit("left-code", "script", &["create-script"], code("left")),
    )
    .await;
    applied(
        &right,
        edit("right-code", "script", &["create-script"], code("right")),
    )
    .await;
    for (store, id, body) in [
        (&left, "left-note", "Left note"),
        (&right, "right-note", "Right note"),
    ] {
        applied(
            store,
            Command::EditResource {
                operation_id: id.into(),
                workspace_id: "work".into(),
                resource_id: "note".into(),
                expected_heads: vec!["create-note".into()],
                title: "Note".into(),
                body: body.into(),
            },
        )
        .await;
    }
    left.import(
        &right.device_id(),
        &right.operations(&["work".into()]).await.unwrap(),
    )
    .await
    .unwrap();
    right
        .import(
            &left.device_id(),
            &left.operations(&["work".into()]).await.unwrap(),
        )
        .await
        .unwrap();
    let merged = left.snapshot().await.unwrap();
    assert_eq!(merged.resources, right.snapshot().await.unwrap().resources);
    assert!(
        merged
            .resources
            .iter()
            .all(|r| r.heads.len() == 2 && r.history.len() == 3)
    );
    assert!(matches!(
        left.command(edit("stale", "script", &["left-code"], code("overwrite")))
            .await
            .unwrap(),
        CommandReceipt::Conflict { .. }
    ));
    applied(
        &left,
        edit(
            "resolved",
            "script",
            &["right-code", "left-code"],
            code("both reviewed"),
        ),
    )
    .await;
    right
        .import(
            &left.device_id(),
            &left.operations(&["work".into()]).await.unwrap(),
        )
        .await
        .unwrap();
    let final_state = right.snapshot().await.unwrap();
    let script = final_state
        .resources
        .iter()
        .find(|r| r.id == "script")
        .unwrap();
    assert_eq!(script.history.len(), 4);
    assert_eq!(script.heads.len(), 1);
    let Revision::Surface(revision) = &script.heads[0] else {
        panic!("code became a note")
    };
    assert_eq!(revision.surface, code("both reviewed"));
    assert_eq!(
        final_state
            .resources
            .iter()
            .find(|r| r.id == "note")
            .unwrap()
            .heads
            .len(),
        2
    );
}

#[tokio::test]
async fn resource_kind_and_target_validation_fail_before_any_commit() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "Local").await.unwrap();
    origin(&store).await;
    applied(&store, create("script", code("saved"))).await;
    applied(
        &store,
        Command::CreateResource {
            operation_id: "create-note".into(),
            workspace_id: "work".into(),
            resource_id: "note".into(),
            title: "Note".into(),
            body: "saved".into(),
        },
    )
    .await;
    let before = store.snapshot().await.unwrap();
    let invalid = [
        create(
            "bad-script",
            Surface::Application {
                application: WebApplication::Website,
                url: "javascript:alert(1)".into(),
            },
        ),
        create(
            "bad-http",
            Surface::Application {
                application: WebApplication::Website,
                url: "http://example.org/".into(),
            },
        ),
        create(
            "bad-secret",
            Surface::Document {
                application: WebApplication::Word,
                url: "https://user:secret@example.org/".into(),
            },
        ),
        create(
            "bad-space",
            Surface::Document {
                application: WebApplication::Website,
                url: "https://exam\nple.org/".into(),
            },
        ),
        create(
            "bad-slash",
            Surface::Document {
                application: WebApplication::Website,
                url: "https://example.org\\@other.org/".into(),
            },
        ),
        create("bad-size", code(&"x".repeat(128 * 1024 + 1))),
        create("bad-null", code("a\0b")),
        edit(
            "note-to-surface",
            "note",
            &["create-note"],
            code("replacement"),
        ),
        Command::EditResource {
            operation_id: "surface-to-note".into(),
            workspace_id: "work".into(),
            resource_id: "script".into(),
            expected_heads: vec!["create-script".into()],
            title: "Oops".into(),
            body: "replacement".into(),
        },
    ];
    for command in invalid {
        assert!(matches!(
            store.command(command).await.unwrap(),
            CommandReceipt::Rejected { .. }
        ));
        assert_eq!(store.snapshot().await.unwrap(), before);
    }
}

#[test]
fn external_surface_and_revision_shapes_are_exclusive() {
    for payload in [
        r#"{"type":"document","application":"word"}"#,
        r#"{"type":"application","application":"unknown","url":"https://example.org/"}"#,
        r#"{"type":"code_document","language":"unknown","text":"x"}"#,
        r#"{"type":"code_document","language":"python","text":"x","url":"https://example.org/"}"#,
        r#"{"type":"unknown","application":"word","url":"https://example.org/"}"#,
    ] {
        assert!(serde_json::from_str::<Surface>(payload).is_err());
    }
    let mixed = r#"{"id":"rev","author":"device","title":"Mixed","parents":[],"body":"note","surface":{"type":"code_document","language":"python","text":"x"}}"#;
    assert!(serde_json::from_str::<Revision>(mixed).is_err());
}

#[tokio::test]
async fn valid_signatures_do_not_make_invalid_remote_targets_acceptable() {
    let left_dir = tempfile::tempdir().unwrap();
    let right_dir = tempfile::tempdir().unwrap();
    let left = Store::open(left_dir.path(), "Left").await.unwrap();
    let right = Store::open(right_dir.path(), "Right").await.unwrap();
    origin(&left).await;
    enroll(&left, &right).await;
    let before = left.snapshot().await.unwrap();
    let invalid = SignedOperation::sign(
        Operation {
            id: "signed-invalid-url".into(),
            workspace_id: "work".into(),
            author: right.device_id(),
            parents: right.snapshot().await.unwrap().workspaces[0].heads.clone(),
            action: Action::CreateSurface {
                resource_id: "bad".into(),
                title: "Bad URL".into(),
                surface: Surface::Application {
                    application: WebApplication::Website,
                    url: "data:text/html,invalid".into(),
                },
            },
        },
        &right.secret_key(),
    )
    .unwrap();
    let mut operations = right.operations(&["work".into()]).await.unwrap();
    operations.push(invalid);
    assert!(left.import(&right.device_id(), &operations).await.is_err());
    assert_eq!(left.snapshot().await.unwrap(), before);
}

/// Raw v1 JSON is signed independently of the current serde model so field
/// reordering or extra default fields in legacy actions cannot hide in this test.
fn legacy_signed(raw: &str, key: &SecretKey) -> SignedOperation {
    let mut bytes = b"workspace/operation/v1\0".to_vec();
    bytes.extend_from_slice(raw.as_bytes());
    let signature = hex::encode(key.sign(&bytes).to_bytes());
    let envelope = format!(r#"{{"version":1,"operation":{raw},"signature":"{signature}"}}"#);
    let signed: SignedOperation = serde_json::from_str(&envelope).unwrap();
    signed.verify().unwrap();
    assert_eq!(serde_json::to_string(&signed.operation).unwrap(), raw);
    signed
}

#[tokio::test]
async fn legacy_signed_note_bytes_bootstrap_reopen_and_mix_with_new_surfaces() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "Current").await.unwrap();
    let key = SecretKey::from_bytes(&[71; 32]);
    let author = key.public().to_string();
    let member = store.device_id();
    let raw = [
        format!(
            r#"{{"id":"legacy-root","workspace_id":"work","author":"{author}","parents":[],"action":{{"type":"create_workspace","title":"Legacy"}}}}"#
        ),
        format!(
            r#"{{"id":"legacy-note","workspace_id":"work","author":"{author}","parents":["legacy-root"],"action":{{"type":"create_resource","resource_id":"note","title":"Legacy note","body":"Original"}}}}"#
        ),
        format!(
            r#"{{"id":"legacy-edit","workspace_id":"work","author":"{author}","parents":["legacy-note"],"action":{{"type":"edit_resource","resource_id":"note","expected_heads":["legacy-note"],"title":"Legacy note","body":"Kept"}}}}"#
        ),
        format!(
            r#"{{"id":"legacy-grant","workspace_id":"work","author":"{author}","parents":["legacy-edit"],"action":{{"type":"grant_member","member_id":"{member}"}}}}"#
        ),
    ];
    let operations: Vec<_> = raw.iter().map(|raw| legacy_signed(raw, &key)).collect();
    store
        .bootstrap(
            contact(&key, "Old host"),
            vec!["work".into()],
            operations.clone(),
        )
        .await
        .unwrap();
    applied(&store, create("script", code("from the new host"))).await;
    drop(store);
    let reopened = Store::open(directory.path(), "Current").await.unwrap();
    let snapshot = reopened.snapshot().await.unwrap();
    let note = snapshot.resources.iter().find(|r| r.id == "note").unwrap();
    assert_eq!(note_body(&note.heads[0]), "Kept");
    assert_eq!(note.history.len(), 2);
    let note_json = serde_json::to_value(&note.heads[0]).unwrap();
    assert_eq!(note_json["body"], "Kept");
    assert!(note_json.get("surface").is_none());
    let exported = reopened.operations(&["work".into()]).await.unwrap();
    for original in operations {
        assert_eq!(
            exported
                .iter()
                .find(|op| op.operation.id == original.operation.id)
                .unwrap(),
            &original
        );
    }
    assert_eq!(snapshot.resources.len(), 2);
}
