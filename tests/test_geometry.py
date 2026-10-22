"""Window placement and screenshot coordinates."""

from assistant.geometry import Shot, fit, half, region, to_monitor
from winapi.shapes import Rect

WORK = Rect(0, 0, 1920, 1040)  # a 1080p monitor, minus the taskbar
SECOND = Rect(1920, 0, 1920 + 2560, 1400)


def test_half_fills_one_side_of_the_work_area_counting_invisible_borders() -> None:
    # Windows 10/11 windows have ~7px invisible borders around what's drawn.
    outer, visible = Rect(100, 100, 914, 708), Rect(107, 100, 907, 701)
    assert half(WORK, "left", outer, visible) == Rect(-7, 0, 967, 1047)
    assert half(WORK, "right", outer, visible) == Rect(953, 0, 1927, 1047)


def test_to_monitor_keeps_the_window_in_the_same_place_relative_to_the_screen() -> None:
    window = Rect(480, 260, 1440, 780)  # centred, half the size of WORK
    assert to_monitor(window, WORK, SECOND) == Rect(1920 + 640, 350, 1920 + 1920, 1050)


def test_to_monitor_keeps_windows_on_screen() -> None:
    window = Rect(1800, 900, 2400, 1300)  # hanging off the bottom right
    moved = to_monitor(window, WORK, SECOND)
    assert SECOND.left <= moved.left and moved.right <= SECOND.right
    assert SECOND.top <= moved.top and moved.bottom <= SECOND.bottom


def test_fit_shrinks_to_a_maximum_and_keeps_the_shape() -> None:
    assert fit(2560, 1440, 1280) == (1280, 720)
    assert fit(800, 600, 1280) == (800, 600)


def test_shot_maps_image_points_back_to_the_screen() -> None:
    shot = Shot(Rect(1920, 0, 1920 + 2560, 1440), 1280, 720)
    assert shot.to_screen(0, 0) == (1920, 0)
    assert shot.to_screen(640, 360) == (1920 + 1280, 720)


def test_region_from_a_drag_in_any_direction() -> None:
    assert region((300, 400), (100, 150)) == Rect(100, 150, 301, 401)
