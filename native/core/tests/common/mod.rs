use workspace_core::model::Revision;

pub fn note_body(revision: &Revision) -> &str {
    match revision {
        Revision::Note(note) => &note.body,
        Revision::Surface(_) | Revision::Work(_) => panic!("expected a note revision"),
    }
}
