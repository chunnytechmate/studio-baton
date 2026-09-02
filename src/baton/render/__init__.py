"""Turning validated data into documents and messages.

Everything here is deterministic: the same input always produces the same
output. That is what makes a published document reviewable: a difference in
the result means a difference in the data, never a difference in a model's mood.
"""
