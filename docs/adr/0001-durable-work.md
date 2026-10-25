# Recover tasks from recorded outcomes

Toolkit's accepted automation architecture gives tasks a lifetime independent
of conversation views and model requests. A transactional local task journal
records goals, runs, checkpoints, user responses, and consequential action
attempts. Run identities fence stale execution updates; action outcomes can be
recorded after cancellation without changing the task's terminal state.

Recovery uses bounded progress facts and fresh inspection of affected state.
Raw SDK histories remain in memory because they can contain screenshots,
browser and Teams payloads, credentials, and encrypted reasoning. Reopening the
journal therefore does not replay a pending model tool call. This trades exact
conversation continuation for explicit recovery and preserves the existing
integration-journal policy.
