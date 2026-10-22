"""Reading time depends on content and observations, never the real clock."""

from assistant.notification_reading import START_SECONDS, Finished, Paused, Scrolling, Waiting, advance, displayed, pace


def test_reading_waits_then_moves_at_a_bounded_pace_to_the_last_line() -> None:
    state = Waiting(0, 10)
    assert advance(state, 9, 500, 20, paused=False) == state
    assert advance(state, 10, 500, 20, paused=False) == Scrolling(0, 10)
    assert advance(state, 12, 500, 20, paused=False) == Scrolling(40, 12)
    assert advance(Scrolling(40, 12), 15, 500, 20, paused=False) == Scrolling(100, 15)
    assert advance(Scrolling(40, 12), 12, 500, 20, paused=False) == Scrolling(40, 12)
    assert advance(Scrolling(40, 12), 100, 500, 20, paused=False) == Finished(500)


def test_hover_or_another_reply_pauses_without_catching_up_on_resume() -> None:
    state = advance(Scrolling(40, 12), 20, 500, 20, paused=True)
    assert state == Paused(40)
    assert advance(state, 10000, 500, 20, paused=True) == state
    resumed = advance(state, 10001, 500, 20, paused=False)
    assert resumed == Waiting(40, 10001 + START_SECONDS)
    assert advance(resumed, 10001 + START_SECONDS + 1, 500, 20, paused=False).offset == 60


def test_growing_a_finished_reply_resumes_from_the_last_read_position() -> None:
    assert advance(Finished(100), 10, 300, 20, paused=False) == Waiting(100, 10 + START_SECONDS)
    assert advance(Scrolling(100, 10), 11, 80, 20, paused=False) == Finished(80)
    assert advance(Waiting(0, 10), 5, 0, 20, paused=False) == Finished(0)


def test_reduced_motion_pages_overlap_and_still_reach_the_end() -> None:
    assert displayed(179, 500, 200, 20, smooth=False) == 0
    assert displayed(180, 500, 200, 20, smooth=False) == 180
    assert displayed(499, 500, 200, 20, smooth=False) == 360
    assert displayed(500, 500, 200, 20, smooth=False) == 500
    assert displayed(12.5, 500, 200, 20, smooth=True) == 12.5
    assert displayed(75, 500, 200, 20, smooth=False, origin=75) == 75
    assert displayed(150, 500, 200, 20, smooth=False, origin=75) == 75
    assert displayed(255, 500, 200, 20, smooth=False, origin=75) == 255


def test_prose_reads_at_about_180_words_per_minute_with_sparse_and_dense_bounds() -> None:
    assert pace("detail " * 180, 720, 24) == 720 * 3 / 210
    assert pace("word", 10000, 24) == 24
    assert pace("字" * 10000, 200, 24) == 6
