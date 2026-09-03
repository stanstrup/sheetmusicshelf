"""A corrected adapter has to be able to withdraw what it used to say.

Keeping every signal for ever sounds conservative and is the opposite: a value
an adapter was fixed to stop emitting goes on arguing its case, and re-scanning
with the fix changes nothing.
"""

from __future__ import annotations

from sms.ingest.persist import DENORMALISED, _INT_COLUMNS, is_superseded


def _reading(field, source, value):
    return (field, source, str(value))


class TestSupersededReadings:
    def test_a_reading_still_made_survives(self):
        still = {_reading("title", "folder_form", "Kreisleriana")}
        assert not is_superseded(_reading("title", "folder_form", "Kreisleriana"), still)

    def test_the_same_source_changing_its_mind_supersedes_the_old_value(self):
        still = {_reading("title", "path_pattern", "Children's Corner")}
        assert is_superseded(_reading("title", "path_pattern", "Children's Corner, no. 1"), still)

    def test_a_field_no_longer_claimed_at_all_is_withdrawn(self):
        # Schumann's Kreisleriana stayed eight works rather than one because a
        # stale "Op. 16 no. 3" outlived the reading that produced it, and works
        # group by catalogue before title.
        still = {_reading("title", "single_work_folder", "Kreisleriana")}
        assert is_superseded(_reading("catalog", "path_pattern", "Op. 16 no. 3"), still)

    def test_a_different_source_agreeing_is_not_the_same_reading(self):
        # Two sources reaching the same value are two signals, and the scorer
        # is entitled to count both.
        still = {_reading("form", "folder_form", "Sonata")}
        assert is_superseded(_reading("form", "filename_form", "Sonata"), still)


class TestDenormalisedColumns:
    def test_every_integer_column_is_a_real_column(self):
        assert _INT_COLUMNS <= set(DENORMALISED.values())

    def test_the_movement_lands_on_the_movement_column(self):
        assert DENORMALISED["movement_no"] == "movement"
