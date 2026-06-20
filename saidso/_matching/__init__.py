"""Internal SPOKEN/CONFIRMED matching engine — normalization + fuzzy comparison.

Private to saidso (note the leading underscore): the deterministic machinery the
``SPOKEN`` / ``CONFIRMED`` / ``INFERABLE`` policies use to decide whether a value
appears in the transcript. Not part of the public API; may change without notice.
"""
