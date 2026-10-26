//! Pure meeting-work values and transitions shared by native and browser hosts.
use super::*;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceReference {
    pub workspace_id: String,
    pub resource_id: String,
    pub revision_id: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvidenceCitation {
    pub source: SourceReference,
    pub quote: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum EvidenceBasis {
    Transcript { citations: Vec<EvidenceCitation> },
    User,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ActionOwner {
    Unassigned,
    Assigned { name: String, basis: EvidenceBasis },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ActionDeadline {
    Unspecified,
    Stated { text: String, basis: EvidenceBasis },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeetingAction {
    pub id: String,
    pub text: String,
    pub owner: ActionOwner,
    pub deadline: ActionDeadline,
    pub basis: EvidenceBasis,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkRun {
    pub id: String,
    pub originating_device: String,
    pub started_at_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeetingMaterial {
    pub reference: SourceReference,
    pub citations: Vec<EvidenceCitation>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum MeetingFollowUp {
    UnsentText { material: MeetingMaterial },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum MeetingWorkState {
    Requested,
    Preparing {
        run: WorkRun,
    },
    NeedsInput {
        run: WorkRun,
        question: String,
    },
    MaterialsReady {
        run: WorkRun,
        notes: MeetingMaterial,
        actions: Vec<MeetingAction>,
        follow_up: MeetingFollowUp,
    },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Work {
    Meeting {
        outcome: String,
        sources: Vec<SourceReference>,
        state: MeetingWorkState,
    },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkRevision {
    pub id: String,
    pub author: String,
    pub title: String,
    pub work: Work,
    pub parents: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkChange {
    ReviseRequest {
        title: String,
        outcome: String,
        sources: Vec<SourceReference>,
    },
    BeginPreparation {
        run_id: String,
        started_at_ms: u64,
    },
    NeedInput {
        run_id: String,
        question: String,
    },
    CorrectActions {
        actions: Vec<MeetingAction>,
    },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeetingMaterialInput {
    pub resource_id: String,
    pub title: String,
    pub body: String,
    pub citations: Vec<EvidenceCitation>,
}

fn text(value: &str, maximum: usize) -> Result<()> {
    ensure!(
        !value.trim().is_empty() && value.len() <= maximum && !value.contains('\0'),
        "invalid meeting work text"
    );
    Ok(())
}
fn reference(value: &SourceReference) -> Result<()> {
    validate_id(&value.workspace_id)?;
    validate_id(&value.resource_id)?;
    validate_id(&value.revision_id)
}
fn sources(values: &[SourceReference]) -> Result<()> {
    ensure!(values.len() <= 16, "too many transcript sources");
    let mut seen = BTreeSet::new();
    for value in values {
        reference(value)?;
        ensure!(seen.insert(value), "duplicate transcript source");
    }
    Ok(())
}
fn citations(values: &[EvidenceCitation]) -> Result<()> {
    ensure!(
        !values.is_empty() && values.len() <= 16,
        "meeting evidence requires 1 to 16 citations"
    );
    for value in values {
        reference(&value.source)?;
        text(&value.quote, 2048)?;
    }
    Ok(())
}
fn basis(value: &EvidenceBasis, generated: bool, literal: Option<&str>) -> Result<()> {
    match value {
        EvidenceBasis::Transcript { citations: values } => {
            citations(values)?;
            if let Some(literal) = literal {
                ensure!(
                    values.iter().any(|value| value.quote.contains(literal)),
                    "stated owner or deadline is absent from its evidence"
                );
            }
        }
        EvidenceBasis::User => ensure!(
            !generated,
            "generated material cannot claim user correction"
        ),
    }
    Ok(())
}
fn actions(values: &[MeetingAction], generated: bool) -> Result<()> {
    ensure!(values.len() <= 64, "too many assigned actions");
    let mut seen = BTreeSet::new();
    for value in values {
        validate_id(&value.id)?;
        ensure!(seen.insert(&value.id), "duplicate meeting action");
        text(&value.text, 4096)?;
        basis(&value.basis, generated, None)?;
        if let ActionOwner::Assigned {
            name,
            basis: evidence,
        } = &value.owner
        {
            validate_title(name)?;
            text(name, 120)?;
            basis(evidence, generated, Some(name))?;
        }
        if let ActionDeadline::Stated {
            text: wording,
            basis: evidence,
        } = &value.deadline
        {
            text(wording, 4096)?;
            basis(evidence, generated, Some(wording))?;
        }
    }
    Ok(())
}
fn material(value: &MeetingMaterialInput) -> Result<()> {
    validate_id(&value.resource_id)?;
    validate_title(&value.title)?;
    validate_body(&value.body)?;
    text(&value.body, MAX_DOCUMENT_BYTES)?;
    citations(&value.citations)
}
fn heads(resource: &str, expected: &[String]) -> Result<()> {
    validate_id(resource)?;
    validate_list(expected, 64)?;
    ensure!(!expected.is_empty(), "work edit requires observed heads");
    Ok(())
}

pub(super) fn validate_create(
    resource: &str,
    title: &str,
    outcome: &str,
    selected: &[SourceReference],
) -> Result<()> {
    validate_id(resource)?;
    validate_title(title)?;
    text(outcome, 4096)?;
    sources(selected)
}
pub(super) fn validate_change(
    resource: &str,
    expected: &[String],
    change: &WorkChange,
) -> Result<()> {
    heads(resource, expected)?;
    match change {
        WorkChange::ReviseRequest {
            title,
            outcome,
            sources,
        } => validate_create(resource, title, outcome, sources),
        WorkChange::BeginPreparation {
            run_id,
            started_at_ms,
        } => {
            validate_id(run_id)?;
            ensure!(
                *started_at_ms <= 9_007_199_254_740_991,
                "invalid run observation time"
            );
            Ok(())
        }
        WorkChange::NeedInput { run_id, question } => {
            validate_id(run_id)?;
            text(question, 4096)
        }
        WorkChange::CorrectActions { actions: values } => actions(values, false),
    }
}
pub(super) fn validate_publish(
    resource: &str,
    expected: &[String],
    run: &str,
    notes: &MeetingMaterialInput,
    values: &[MeetingAction],
    follow_up: &MeetingMaterialInput,
) -> Result<()> {
    heads(resource, expected)?;
    validate_id(run)?;
    material(notes)?;
    material(follow_up)?;
    actions(values, true)?;
    ensure!(
        notes.resource_id != follow_up.resource_id
            && notes.resource_id != resource
            && follow_up.resource_id != resource,
        "publication resource identities must be distinct"
    );
    ensure!(
        serde_json::to_vec(&(notes, values, follow_up))?.len() <= 512 * 1024,
        "meeting publication exceeds its bound"
    );
    Ok(())
}

fn transcript<'a>(
    projection: &'a Projection,
    op: &Operation,
    past: &BTreeSet<String>,
    source: &SourceReference,
) -> Result<&'a str> {
    ensure!(
        source.workspace_id == op.workspace_id,
        "meeting source belongs to another workspace"
    );
    ensure!(
        past.contains(&source.revision_id),
        "meeting source is not in the causal past"
    );
    let resource = projection
        .resources
        .get(&source.resource_id)
        .context("meeting source is absent")?;
    ensure!(
        resource.workspace_id == op.workspace_id,
        "meeting source origin differs from workspace"
    );
    let revision = resource
        .history
        .iter()
        .find(|revision| revision.id() == source.revision_id)
        .context("pinned transcript revision is absent")?;
    match revision {
        Revision::Note(note) => Ok(&note.body),
        _ => bail!("meeting source is not transcript text"),
    }
}
fn check_sources(
    projection: &Projection,
    op: &Operation,
    past: &BTreeSet<String>,
    selected: &[SourceReference],
) -> Result<()> {
    for source in selected {
        transcript(projection, op, past, source)?;
    }
    Ok(())
}
fn check_citations(
    projection: &Projection,
    op: &Operation,
    past: &BTreeSet<String>,
    selected: &[SourceReference],
    values: &[EvidenceCitation],
) -> Result<()> {
    for evidence in values {
        ensure!(
            selected.contains(&evidence.source),
            "citation is outside the captured transcript selection"
        );
        ensure!(
            transcript(projection, op, past, &evidence.source)?.contains(&evidence.quote),
            "citation quote is absent from the pinned transcript"
        );
    }
    Ok(())
}
fn check_actions(
    projection: &Projection,
    op: &Operation,
    past: &BTreeSet<String>,
    selected: &[SourceReference],
    values: &[MeetingAction],
) -> Result<()> {
    for value in values {
        let mut evidence = vec![&value.basis];
        if let ActionOwner::Assigned { basis, .. } = &value.owner {
            evidence.push(basis);
        }
        if let ActionDeadline::Stated { basis, .. } = &value.deadline {
            evidence.push(basis);
        }
        for basis in evidence {
            if let EvidenceBasis::Transcript { citations } = basis {
                check_citations(projection, op, past, selected, citations)?;
            }
        }
    }
    Ok(())
}
fn observed_work(
    projection: &Projection,
    op: &Operation,
    past: &BTreeSet<String>,
    resource_id: &str,
    expected: &[String],
) -> Result<Vec<WorkRevision>> {
    let resource = projection
        .resources
        .get(resource_id)
        .context("work is absent")?;
    ensure!(
        resource.workspace_id == op.workspace_id,
        "work belongs to another workspace"
    );
    let known = revision_heads(
        resource
            .history
            .iter()
            .filter(|revision| past.contains(revision.id()))
            .cloned(),
    );
    ensure!(
        !known.is_empty()
            && known
                .iter()
                .map(|revision| revision.id())
                .collect::<BTreeSet<_>>()
                == expected.iter().map(String::as_str).collect(),
        "work edit does not name causal heads"
    );
    known
        .into_iter()
        .map(|revision| match revision {
            Revision::Work(work) => Ok(*work),
            _ => bail!("resource is not work"),
        })
        .collect()
}
fn work_revision(
    op: &Operation,
    title: String,
    outcome: String,
    sources: Vec<SourceReference>,
    state: MeetingWorkState,
    parents: Vec<String>,
) -> Revision {
    Revision::Work(Box::new(WorkRevision {
        id: op.id.clone(),
        author: op.author.clone(),
        title,
        work: Work::Meeting {
            outcome,
            sources,
            state,
        },
        parents,
    }))
}
fn owned_run<'a>(state: &'a MeetingWorkState, run_id: &str, author: &str) -> Result<&'a WorkRun> {
    let MeetingWorkState::Preparing { run } = state else {
        bail!("meeting work is not preparing")
    };
    ensure!(
        run.id == run_id && run.originating_device == author,
        "publication does not own this run"
    );
    Ok(run)
}

pub(super) fn project_create(
    projection: &mut Projection,
    op: &Operation,
    past: &BTreeSet<String>,
    resource: &str,
    title: &str,
    outcome: &str,
    selected: &[SourceReference],
) -> Result<()> {
    check_sources(projection, op, past, selected)?;
    create_resource(
        projection,
        op,
        resource,
        work_revision(
            op,
            title.into(),
            outcome.into(),
            selected.to_vec(),
            MeetingWorkState::Requested,
            vec![],
        ),
    )
}
pub(super) fn project_edit(
    projection: &mut Projection,
    op: &Operation,
    past: &BTreeSet<String>,
    resource: &str,
    expected: &[String],
    change: &WorkChange,
) -> Result<()> {
    let observed = observed_work(projection, op, past, resource, expected)?;
    let first = &observed[0];
    let Work::Meeting {
        outcome,
        sources,
        state,
    } = &first.work;
    let (title, outcome, sources, next) = match change {
        WorkChange::ReviseRequest {
            title,
            outcome,
            sources,
        } => {
            check_sources(projection, op, past, sources)?;
            (
                title.clone(),
                outcome.clone(),
                sources.clone(),
                MeetingWorkState::Requested,
            )
        }
        WorkChange::BeginPreparation {
            run_id,
            started_at_ms,
        } => {
            ensure!(
                observed.len() == 1 && !sources.is_empty(),
                "preparation needs one work revision with transcript sources"
            );
            ensure!(
                matches!(
                    state,
                    MeetingWorkState::Requested | MeetingWorkState::NeedsInput { .. }
                ),
                "preparation cannot take over an existing run"
            );
            let history = &projection
                .resources
                .get(resource)
                .context("work is absent")?
                .history;
            let used = history
                .iter()
                .filter(|revision| past.contains(revision.id()))
                .any(|revision| {
                    let Revision::Work(work) = revision else {
                        return false;
                    };
                    let Work::Meeting { state, .. } = &work.work;
                    match state {
                        MeetingWorkState::Requested => false,
                        MeetingWorkState::Preparing { run }
                        | MeetingWorkState::NeedsInput { run, .. }
                        | MeetingWorkState::MaterialsReady { run, .. } => run.id == *run_id,
                    }
                });
            ensure!(!used, "a new preparation requires a new run identity");
            (
                first.title.clone(),
                outcome.clone(),
                sources.clone(),
                MeetingWorkState::Preparing {
                    run: WorkRun {
                        id: run_id.clone(),
                        originating_device: op.author.clone(),
                        started_at_ms: *started_at_ms,
                    },
                },
            )
        }
        WorkChange::NeedInput { run_id, question } => {
            ensure!(
                observed.len() == 1,
                "interrupted work has conflicting revisions"
            );
            let run = owned_run(state, run_id, &op.author)?.clone();
            (
                first.title.clone(),
                outcome.clone(),
                sources.clone(),
                MeetingWorkState::NeedsInput {
                    run,
                    question: question.clone(),
                },
            )
        }
        WorkChange::CorrectActions { actions } => {
            let MeetingWorkState::MaterialsReady {
                run,
                notes,
                follow_up,
                ..
            } = state
            else {
                bail!("actions are not ready for correction")
            };
            for work in &observed {
                let Work::Meeting {
                    outcome: other_outcome,
                    sources: other_sources,
                    state: other_state,
                } = &work.work;
                ensure!(
                    other_outcome == outcome
                        && other_sources == sources
                        && work.title == first.title,
                    "conflicting work requests require review"
                );
                let MeetingWorkState::MaterialsReady {
                    run: other_run,
                    notes: other_notes,
                    follow_up: other_follow_up,
                    ..
                } = other_state
                else {
                    bail!("conflicting material states require review")
                };
                ensure!(
                    run == other_run && notes == other_notes && follow_up == other_follow_up,
                    "different published materials require review"
                );
            }
            check_actions(projection, op, past, sources, actions)?;
            (
                first.title.clone(),
                outcome.clone(),
                sources.clone(),
                MeetingWorkState::MaterialsReady {
                    run: run.clone(),
                    notes: notes.clone(),
                    actions: actions.clone(),
                    follow_up: follow_up.clone(),
                },
            )
        }
    };
    edit_resource(
        projection,
        op,
        resource,
        past,
        work_revision(op, title, outcome, sources, next, expected.to_vec()),
    )
}
pub(super) fn project_publish(
    projection: &mut Projection,
    op: &Operation,
    past: &BTreeSet<String>,
) -> Result<()> {
    let Action::PublishMeetingMaterials {
        resource_id: resource,
        expected_heads: expected,
        run_id,
        notes,
        actions,
        follow_up,
    } = &op.action
    else {
        bail!("expected meeting publication")
    };
    let observed = observed_work(projection, op, past, resource, expected)?;
    ensure!(
        observed.len() == 1,
        "publication requires one work revision"
    );
    let first = &observed[0];
    let Work::Meeting {
        outcome,
        sources,
        state,
    } = &first.work;
    let run = owned_run(state, run_id, &op.author)?.clone();
    check_citations(projection, op, past, sources, &notes.citations)?;
    check_citations(projection, op, past, sources, &follow_up.citations)?;
    check_actions(projection, op, past, sources, actions)?;
    for output in [notes, follow_up] {
        create_resource(
            projection,
            op,
            &output.resource_id,
            Revision::Note(NoteRevision {
                id: op.id.clone(),
                author: op.author.clone(),
                title: output.title.clone(),
                body: output.body.clone(),
                parents: vec![],
            }),
        )?;
    }
    let material = |input: &MeetingMaterialInput| MeetingMaterial {
        reference: SourceReference {
            workspace_id: op.workspace_id.clone(),
            resource_id: input.resource_id.clone(),
            revision_id: op.id.clone(),
        },
        citations: input.citations.clone(),
    };
    let ready = MeetingWorkState::MaterialsReady {
        run,
        notes: material(notes),
        actions: actions.to_vec(),
        follow_up: MeetingFollowUp::UnsentText {
            material: material(follow_up),
        },
    };
    edit_resource(
        projection,
        op,
        resource,
        past,
        work_revision(
            op,
            first.title.clone(),
            outcome.clone(),
            sources.clone(),
            ready,
            expected.to_vec(),
        ),
    )
}

pub(crate) fn command_matches(command: &Command, action: &Action) -> bool {
    fn same_heads(left: &[String], right: &[String]) -> bool {
        left.iter().collect::<BTreeSet<_>>() == right.iter().collect()
    }
    match (command, action) {
        (
            Command::CreateWork {
                resource_id: a,
                title: at,
                outcome: ao,
                sources: s,
                ..
            },
            Action::CreateWork {
                resource_id: b,
                title: bt,
                outcome: bo,
                sources: t,
            },
        ) => a == b && at == bt && ao == bo && s == t,
        (
            Command::EditWork {
                resource_id: a,
                expected_heads: ah,
                change: c,
                ..
            },
            Action::EditWork {
                resource_id: b,
                expected_heads: bh,
                change: d,
            },
        ) => a == b && same_heads(ah, bh) && c == d,
        (
            Command::PublishMeetingMaterials {
                resource_id: a,
                expected_heads: ah,
                run_id: ar,
                notes: an,
                actions: aa,
                follow_up: af,
                ..
            },
            Action::PublishMeetingMaterials {
                resource_id: b,
                expected_heads: bh,
                run_id: br,
                notes: bn,
                actions: ba,
                follow_up: bf,
            },
        ) => a == b && same_heads(ah, bh) && ar == br && an == bn && aa == ba && af == bf,
        _ => false,
    }
}
