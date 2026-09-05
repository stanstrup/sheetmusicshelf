"""Free-text search: a phrase of words, not one literal string.

"mozart fantasy" found nothing at all. The whole phrase was matched as a single
substring against one column at a time, and no title contains "mozart fantasy",
nor does any composer -- so the most natural question anyone asks of a music
catalogue was the one question it could not answer.
"""

from __future__ import annotations

import pytest

from sms.catalog_query import MAX_TERMS, Filters, base_query, narrow
from sms.models import Piece, SourceFile
from sms.music.synonyms import expand
from sqlalchemy import func, select


def hits(session, text: str) -> list[Piece]:
    query = narrow(base_query(), Filters(q=text), text_match="exact")
    return list(session.scalars(query))


@pytest.fixture
def catalogue(session, collection):
    """A few pieces that between them cover every way search can go wrong."""
    rows = [
        ("Fantasy in D Minor", "Wolfgang Amadeus Mozart", "K. 397", None, "Sonatas and Fantasies"),
        ("Fantasia in F Minor", "Wolfgang Amadeus Mozart", "K. 608", "F minor", None),
        ("Sonata No. 1 in C Major", "Wolfgang Amadeus Mozart", "K. 279", "C major", "Sonatas and Fantasies"),
        ("Nocturne, Op. 27 no. 2", "Frederic Chopin", "Op. 27 no. 2", None, "Nocturne"),
        ("Etude, Op. 10 no. 1", "Frederic Chopin", "Op. 10 no. 1", None, "Etude"),
    ]
    for index, (title, composer, catalog, key, form) in enumerate(rows):
        source = SourceFile(
            collection_id=collection.id, rel_path=f"f{index}.pdf", page_count=2, size=1
        )
        session.add(source)
        session.flush()
        session.add(Piece(
            source_file_id=source.id, page_start=1, page_end=2, title=title,
            composer_name=composer, catalog_display=catalog, music_key=key, form=form,
            route="accept", review_state="pending",
        ))
    session.flush()
    return session


class TestAPhraseSpansFields:
    def test_composer_and_title_together(self, catalogue):
        """The bug that started this: one word is the composer, the other the
        title, and neither field contains both."""
        found = hits(catalogue, "mozart fantasy")
        assert {p.title for p in found} == {"Fantasy in D Minor", "Fantasia in F Minor"}

    def test_every_word_has_to_match_something(self, catalogue):
        assert hits(catalogue, "mozart nocturne") == []

    def test_word_order_does_not_matter(self, catalogue):
        assert {p.id for p in hits(catalogue, "fantasy mozart")} == \
               {p.id for p in hits(catalogue, "mozart fantasy")}

    def test_a_single_word_still_works(self, catalogue):
        assert len(hits(catalogue, "chopin")) == 2

    def test_extra_spaces_are_not_words(self, catalogue):
        assert len(hits(catalogue, "   mozart    fantasy  ")) == 2


class TestSpellings:
    def test_either_spelling_finds_both(self, catalogue):
        """This library holds "Fantasia in F Minor" and "Fantasy in D Minor" by
        the same composer. Whichever a person types, they own both."""
        assert len(hits(catalogue, "mozart fantasia")) == 2
        assert len(hits(catalogue, "mozart fantasy")) == 2

    def test_a_study_is_an_etude(self, catalogue):
        assert [p.title for p in hits(catalogue, "chopin study")] == ["Etude, Op. 10 no. 1"]

    def test_expansion_keeps_the_typed_word_first(self):
        assert expand("study")[0] == "study"
        assert "etude" in expand("study")

    def test_an_unknown_word_expands_to_itself(self):
        assert expand("mazurka") == ["mazurka"]


class TestCatalogueNumbers:
    def test_typed_without_the_punctuation(self, catalogue):
        """A catalogue number is written "K. 279" and typed "k279" as often as not."""
        assert [p.title for p in hits(catalogue, "k279")] == ["Sonata No. 1 in C Major"]

    def test_typed_with_it(self, catalogue):
        assert [p.title for p in hits(catalogue, "K. 279")] == ["Sonata No. 1 in C Major"]

    def test_alongside_a_composer(self, catalogue):
        assert len(hits(catalogue, "mozart k279")) == 1


class TestTheFormFieldIsNotSearched:
    def test_a_volume_name_does_not_drag_in_its_contents(self, catalogue):
        """Every Mozart sonata in the CD Sheet Music set carries the form
        "Sonatas and Fantasies", the name of the volume it came in. Searching
        the form field put eighteen sonatas ahead of the six real fantasies."""
        found = hits(catalogue, "fantasy")
        assert "Sonata No. 1 in C Major" not in {p.title for p in found}


class TestLimits:
    def test_a_pasted_paragraph_does_not_become_a_hundred_clauses(self, catalogue):
        text = " ".join(str(n) for n in range(MAX_TERMS * 4))
        assert hits(catalogue, text) == []       # ran, and matched nothing

    def test_the_key_is_searched(self, catalogue):
        assert [p.title for p in hits(catalogue, "mozart f minor")] == ["Fantasia in F Minor"]
