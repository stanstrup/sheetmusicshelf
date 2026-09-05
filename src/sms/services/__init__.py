"""What the catalogue can be asked to do, independent of who is asking.

Reads already had a shared home: :mod:`sms.catalog_query` narrows the
catalogue once and both the browse page and the API use it, because "two
builders that are supposed to agree will keep drifting, and the failure is
silent".

Writes had none, and drifted exactly as predicted -- three times.  The scorer
was written out twice and the copy that reached the database was missing a
rule.  A file path was resolved three ways and one of them returned 410 for
the whole catalogue.  And approving a piece meant one thing on the review page
and a different, incomplete thing through the API.

Each was found only after it caused a visible failure, and each was fixed by
extracting one function.  This package is that extraction done for the write
side as a whole: a state transition is defined once, and a surface's job is to
work out who is asking and what they meant, not what should happen.
"""
