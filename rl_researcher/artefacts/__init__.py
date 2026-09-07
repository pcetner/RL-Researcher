"""Writers for the documents a run leaves behind.

Each module here turns a finished run's files into one artefact kind. Two exist so far: the run
report (`run_report`) and the project's state page (`state`), built on the shared writer
(`writer`), the per-kind section orders (`layouts`) and the statistics every artefact agrees on
(`report`). The live dashboard, the index, the measurement writer and the diagnosis writer are
not written yet.

They share the block inventory and one stylesheet, so every instance of a kind has the same
shape.
"""
