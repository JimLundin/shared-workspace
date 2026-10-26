use serde_json::{Value, json};
use workspace_core::{
    model::{Command, CommandReceipt, MeetingWorkState, PeerRecord, Revision, Work},
    store::Store,
};

fn command(value: Value) -> Command {
    serde_json::from_value(value).unwrap()
}
async fn applied(store: &Store, value: Value) {
    assert!(matches!(
        store.command(command(value)).await.unwrap(),
        CommandReceipt::Applied {
            replayed: false,
            ..
        }
    ));
}
fn source() -> Value {
    json!({"workspace_id":"work","resource_id":"transcript","revision_id":"source"})
}
fn citation() -> Value {
    json!({"source":source(),"quote":"Alice will send the brief on Friday."})
}
fn actions() -> Value {
    json!([{"id":"action-1","text":"Send the brief","owner":{"type":"assigned","name":"Alice","basis":{"type":"transcript","citations":[citation()]}},"deadline":{"type":"stated","text":"Friday","basis":{"type":"transcript","citations":[citation()]}},"basis":{"type":"transcript","citations":[citation()]}}])
}
fn create_work(id: &str, selected: Value) -> Value {
    json!({"type":"create_work","operation_id":id,"workspace_id":"work","resource_id":"meeting","title":"Meeting","outcome":"Prepare cited notes and an unsent follow-up","sources":selected})
}
fn edit(id: &str, heads: &[&str], change: Value) -> Value {
    json!({"type":"edit_work","operation_id":id,"workspace_id":"work","resource_id":"meeting","expected_heads":heads,"change":change})
}
fn begin(id: &str, heads: &[&str], run: &str) -> Value {
    edit(
        id,
        heads,
        json!({"type":"begin_preparation","run_id":run,"started_at_ms":1234}),
    )
}
fn publish() -> Value {
    json!({"type":"publish_meeting_materials","operation_id":"publish","workspace_id":"work","resource_id":"meeting","expected_heads":["begin"],"run_id":"run-1","notes":{"resource_id":"notes","title":"Meeting notes","body":"Alice will send the brief.","citations":[citation()]},"actions":actions(),"follow_up":{"resource_id":"follow-up","title":"Unsent follow-up","body":"Please send the brief on Friday.","citations":[citation()]}})
}
async fn baseline(store: &Store) {
    applied(store,json!({"type":"create_workspace","operation_id":"root","workspace_id":"work","title":"Work"})).await;
    applied(store,json!({"type":"create_resource","operation_id":"source","workspace_id":"work","resource_id":"transcript","title":"Transcript","body":"Alice will send the brief on Friday."})).await;
    applied(store, create_work("request", json!([source()]))).await;
    applied(store, begin("begin", &["request"], "run-1")).await;
}
async fn unchanged_rejection(store: &Store, value: Value, label: &str) {
    let before = store.snapshot().await.unwrap();
    let receipt = store.command(command(value)).await.unwrap();
    assert!(
        matches!(receipt, CommandReceipt::Rejected { .. }),
        "{label}: {receipt:?}"
    );
    assert_eq!(
        store.snapshot().await.unwrap(),
        before,
        "{label}: partial state escaped"
    );
}
async fn enroll(a: &Store, b: &Store) {
    let contact = |store: &Store, name: &str| PeerRecord {
        id: store.secret_key().public().to_string(),
        name: name.into(),
        address_json: serde_json::to_string(&iroh::EndpointAddr::new(store.secret_key().public()))
            .unwrap(),
    };
    let nonce = "53".repeat(32);
    a.create_invite(nonce.clone(), vec!["work".into()], 1000)
        .await
        .unwrap();
    a.accept_invite(&nonce, contact(b, "B"), 10).await.unwrap();
    b.bootstrap(
        contact(a, "A"),
        vec!["work".into()],
        a.operations(&["work".into()]).await.unwrap(),
    )
    .await
    .unwrap();
}
async fn copy(a: &Store, b: &Store) {
    b.import(
        &a.secret_key().public().to_string(),
        &a.operations(&["work".into()]).await.unwrap(),
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn publication_atomically_retains_outputs_work_and_exact_replay_after_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "A").await.unwrap();
    baseline(&store).await;
    let before = store.snapshot().await.unwrap();
    applied(&store, publish()).await;
    let result = store.snapshot().await.unwrap();
    assert_eq!(result.operation_count, before.operation_count + 1);
    assert_eq!(result.resources.len(), before.resources.len() + 2);
    let work = result.resources.iter().find(|r| r.id == "meeting").unwrap();
    let Revision::Work(revision) = &work.heads[0] else {
        panic!("work missing")
    };
    let Work::Meeting {
        state:
            MeetingWorkState::MaterialsReady {
                run,
                notes,
                follow_up,
                ..
            },
        ..
    } = &revision.work
    else {
        panic!("materials not published")
    };
    assert_eq!(run.originating_device, result.device_id);
    assert_eq!(run.started_at_ms, 1234);
    assert_eq!(notes.reference.revision_id, "publish");
    assert!(
        serde_json::to_string(follow_up)
            .unwrap()
            .contains("unsent_text")
    );
    for id in ["notes", "follow-up"] {
        let resource = result.resources.iter().find(|r| r.id == id).unwrap();
        assert_eq!(resource.heads[0].id(), "publish");
        assert!(
            result.workspaces[0]
                .placements
                .iter()
                .any(|p| p.resource_id == id)
        );
    }
    drop(store);
    let reopened = Store::open(directory.path(), "A").await.unwrap();
    assert_eq!(reopened.snapshot().await.unwrap(), result);
    assert!(matches!(
        reopened.command(command(publish())).await.unwrap(),
        CommandReceipt::Applied { replayed: true, .. }
    ));
    assert_eq!(reopened.snapshot().await.unwrap(), result);
    let mut changed = publish();
    changed["notes"]["body"] = json!("Changed payload");
    unchanged_rejection(&reopened, changed, "changed replay").await;
    applied(&reopened,json!({"type":"remove_placement","operation_id":"hide-notes","workspace_id":"work","resource_id":"notes","source_workspace_id":"work"})).await;
    let hidden = reopened.snapshot().await.unwrap();
    assert!(
        !hidden.workspaces[0]
            .placements
            .iter()
            .any(|p| p.resource_id == "notes")
    );
    assert!(hidden.resources.iter().any(|r| r.id == "notes"));
}

#[tokio::test]
async fn invalid_publications_leave_no_outputs_or_operation_and_stale_heads_conflict() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "A").await.unwrap();
    baseline(&store).await;
    let mut cases = Vec::new();
    let mut p = publish();
    p["run_id"] = json!("wrong-run");
    cases.push(("run", p));
    let mut p = publish();
    p["follow_up"]["resource_id"] = json!("transcript");
    cases.push(("second output collision after first projection", p));
    let mut p = publish();
    p["notes"]["resource_id"] = json!("meeting");
    cases.push(("work output collision", p));
    let mut p = publish();
    p["notes"]["citations"][0]["quote"] = json!("Fabricated source wording");
    cases.push(("quote", p));
    let mut p = publish();
    p["actions"][0]["owner"]["name"] = json!("Invented");
    cases.push(("invented owner", p));
    let mut p = publish();
    p["actions"][0]["deadline"]["text"] = json!("2028-01-01");
    cases.push(("invented deadline", p));
    let mut p = publish();
    p["actions"][0]["basis"] = json!({"type":"user"});
    cases.push(("generated user provenance", p));
    let mut p = publish();
    p["follow_up"]["citations"][0]["source"]["workspace_id"] = json!("private");
    cases.push(("private source", p));
    let mut p = publish();
    p["actions"][0]["basis"]["citations"][0]["source"]["revision_id"] = json!("request");
    cases.push(("wrong source revision", p));
    for (label, p) in cases {
        unchanged_rejection(&store, p, label).await;
    }
    let before = store.snapshot().await.unwrap();
    let mut stale = publish();
    stale["expected_heads"] = json!(["request"]);
    assert!(matches!(
        store.command(command(stale)).await.unwrap(),
        CommandReceipt::Conflict { .. }
    ));
    assert_eq!(store.snapshot().await.unwrap(), before);
    applied(&store, publish()).await;
}

#[tokio::test]
async fn source_scope_run_author_and_request_corrections_are_enforced() {
    let da = tempfile::tempdir().unwrap();
    let db = tempfile::tempdir().unwrap();
    let a = Store::open(da.path(), "A").await.unwrap();
    let b = Store::open(db.path(), "B").await.unwrap();
    baseline(&a).await;
    enroll(&a, &b).await;
    unchanged_rejection(&b, publish(), "other actor cannot publish A run").await;
    unchanged_rejection(&b, begin("takeover", &["begin"], "run-B"), "no takeover").await;
    applied(&a,edit("revise",&["begin"],json!({"type":"revise_request","title":"Changed request","outcome":"Shorter follow-up","sources":[source()]}))).await;
    let before = a.snapshot().await.unwrap();
    assert!(matches!(
        a.command(command(publish())).await.unwrap(),
        CommandReceipt::Conflict { .. }
    ));
    assert_eq!(a.snapshot().await.unwrap(), before);
    unchanged_rejection(
        &a,
        begin("reuse", &["revise"], "run-1"),
        "old run identity cannot restart",
    )
    .await;
    let mut bad = edit(
        "private-reference",
        &["revise"],
        json!({"type":"revise_request","title":"Private","outcome":"Use private text","sources":[source()]}),
    );
    bad["change"]["sources"][0]["workspace_id"] = json!("private");
    unchanged_rejection(&a, bad, "source workspace scope").await;
    let bad = edit(
        "work-as-source",
        &["revise"],
        json!({"type":"revise_request","title":"Not transcript","outcome":"Use work as text","sources":[{"workspace_id":"work","resource_id":"meeting","revision_id":"request"}]}),
    );
    unchanged_rejection(&a, bad, "work cannot become its transcript").await;
    applied(&a, begin("new-begin", &["revise"], "run-2")).await;
}

#[tokio::test]
async fn explicit_user_corrections_without_transcript_wording_replicate_and_conflict() {
    let da = tempfile::tempdir().unwrap();
    let db = tempfile::tempdir().unwrap();
    let a = Store::open(da.path(), "A").await.unwrap();
    let b = Store::open(db.path(), "B").await.unwrap();
    baseline(&a).await;
    applied(&a, publish()).await;
    enroll(&a, &b).await;
    let corrected = |name: &str| {
        let mut values = actions();
        values[0]["owner"] = json!({"type":"assigned","name":name,"basis":{"type":"user"}});
        values[0]["deadline"] =
            json!({"type":"stated","text":"Next Wednesday","basis":{"type":"user"}});
        values
    };
    applied(
        &a,
        edit(
            "A-correction",
            &["publish"],
            json!({"type":"correct_actions","actions":corrected("Beatrice")}),
        ),
    )
    .await;
    applied(
        &b,
        edit(
            "B-correction",
            &["publish"],
            json!({"type":"correct_actions","actions":corrected("Caroline")}),
        ),
    )
    .await;
    copy(&a, &b).await;
    copy(&b, &a).await;
    let snapshot = a.snapshot().await.unwrap();
    let work = snapshot
        .resources
        .iter()
        .find(|r| r.id == "meeting")
        .unwrap();
    assert_eq!(
        work.heads.len(),
        2,
        "concurrent user corrections must not overwrite"
    );
    applied(
        &a,
        edit(
            "reviewed",
            &["A-correction", "B-correction"],
            json!({"type":"correct_actions","actions":corrected("User's final owner")}),
        ),
    )
    .await;
    applied(&b,json!({"type":"edit_resource","operation_id":"shorten","workspace_id":"work","resource_id":"follow-up","expected_heads":["publish"],"title":"Unsent follow-up","body":"A shorter user-written follow-up."})).await;
    copy(&a, &b).await;
    copy(&b, &a).await;
    let result = a.snapshot().await.unwrap();
    let work = result.resources.iter().find(|r| r.id == "meeting").unwrap();
    assert_eq!(work.heads.len(), 1);
    let follow = result
        .resources
        .iter()
        .find(|r| r.id == "follow-up")
        .unwrap();
    let Revision::Note(note) = &follow.heads[0] else {
        panic!("follow-up not note")
    };
    assert_eq!(note.body, "A shorter user-written follow-up.");
    unchanged_rejection(
        &b,
        publish(),
        "remote command API does not replay original author request",
    )
    .await;
}

#[tokio::test]
async fn signed_import_rejects_a_source_outside_the_operations_causal_past() {
    use workspace_core::model::{Action, Operation, SignedOperation};
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path(), "A").await.unwrap();
    baseline(&store).await;
    let before = store.snapshot().await.unwrap();
    let key = store.secret_key();
    let signed = SignedOperation::sign(
        Operation {
            id: "zz-forged".into(),
            workspace_id: "work".into(),
            author: key.public().to_string(),
            parents: vec!["root".into()],
            action: Action::CreateWork {
                resource_id: "invalid-meeting".into(),
                title: "Invalid source dependency".into(),
                outcome: "Use a revision this operation never observed".into(),
                sources: serde_json::from_value(json!([source()])).unwrap(),
            },
        },
        &key,
    )
    .unwrap();
    assert!(
        store
            .import(&key.public().to_string(), &[signed])
            .await
            .is_err(),
        "NONCAUSAL_SOURCE_ACCEPTED"
    );
    assert_eq!(store.snapshot().await.unwrap(), before);
}
