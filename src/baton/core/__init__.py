"""Infrastructure shared by every pipeline: config, state, retries, output.

Nothing in here knows about students, Notion, or LINE. Keeping the vendor-free
half separate is what makes the adapters replaceable.
"""
