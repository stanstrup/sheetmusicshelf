"""The annotation layer's validation rules.

The whole feature rests on two invariants: coordinates are normalised, and the
PDF is never touched. The first is enforced here; the second is structural --
nothing in this module can write a file.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sms.api.annotations import MAX_POINTS_PER_STROKE, MAX_STROKES_PER_PAGE, PageLayer, Stroke


class TestStroke:
    def test_defaults_are_a_usable_pen(self):
        stroke = Stroke(points=[(0.1, 0.2)])
        assert stroke.tool == "pen"
        assert 0 < stroke.width < 0.05

    def test_coordinates_are_clamped_not_rejected(self):
        # A finger sliding off the edge of a phone is normal; losing the whole
        # stroke over it would be maddening.
        stroke = Stroke(points=[(-3.0, 5.0), (0.5, 0.5)])
        assert stroke.points[0] == (0.0, 1.0)
        assert stroke.points[1] == (0.5, 0.5)

    def test_a_stroke_needs_a_point(self):
        with pytest.raises(ValidationError):
            Stroke(points=[])

    def test_a_single_point_is_allowed(self):
        # A tap is a dot.
        assert len(Stroke(points=[(0.4, 0.4)]).points) == 1

    def test_width_must_be_positive_and_sane(self):
        with pytest.raises(ValidationError):
            Stroke(points=[(0.1, 0.1)], width=0)
        with pytest.raises(ValidationError):
            Stroke(points=[(0.1, 0.1)], width=5)

    def test_a_runaway_client_is_capped(self):
        with pytest.raises(ValidationError):
            Stroke(points=[(0.5, 0.5)] * (MAX_POINTS_PER_STROKE + 1))


class TestPageLayer:
    def test_pages_are_one_based(self):
        with pytest.raises(ValidationError):
            PageLayer(page=0)

    def test_an_empty_layer_is_valid(self):
        # It is how a page is erased.
        assert PageLayer(page=1).strokes == []

    def test_too_many_strokes_on_one_page_is_refused(self):
        strokes = [Stroke(points=[(0.1, 0.1)])] * (MAX_STROKES_PER_PAGE + 1)
        with pytest.raises(ValidationError):
            PageLayer(page=1, strokes=strokes)

    def test_round_trips_through_json(self):
        layer = PageLayer(
            page=3,
            strokes=[Stroke(tool="highlighter", color="#1f6feb", points=[(0.1, 0.2), (0.3, 0.4)])],
        )
        restored = PageLayer.model_validate_json(layer.model_dump_json())
        assert restored.page == 3
        assert restored.strokes[0].tool == "highlighter"
        assert restored.strokes[0].points == [(0.1, 0.2), (0.3, 0.4)]
