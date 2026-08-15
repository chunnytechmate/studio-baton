"""Everything that talks to something outside Baton.

Each subpackage defines a Protocol and one or more drivers behind it, chosen by
a single line of configuration. Pipelines depend on the Protocol only, which is
what lets the whole suite run offline against `baton.adapters.fakes`.
"""
