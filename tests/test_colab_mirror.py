"""Tier-1 coverage of the Drive mirror (docs/colab-studies.md): the small files travel every
pass, the heavy checkpoint waits for a slow pass, and a re-copy only happens when the source
actually changed."""

import os
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.tier1]


def _mod():
    from rl_researcher import colab_mirror

    return colab_mirror


def _study_out(root: Path) -> Path:
    cell = root / "conv_ae" / "seed0"
    cell.mkdir(parents=True)
    (root / "study.log").write_text("12:00:00 step 100\n", encoding="utf-8")
    (cell / "progress.json").write_text('{"status": "training"}', encoding="utf-8")
    (cell / "checkpoint.pt").write_bytes(b"\0" * 4096)  # stands in for 100+ MB of Adam state
    (root / "figures").mkdir()
    (root / "figures" / "scorecard.png").write_bytes(b"png")
    return root


def test_fast_pass_skips_the_checkpoint_and_slow_pass_takes_it(tmp_path):
    m = _mod()
    src = _study_out(tmp_path / "out")
    dst = tmp_path / "drive"

    copied, _, skipped = m.mirror_once(src, dst, slow_globs=["checkpoint.pt"], include_slow=False)
    assert copied == 3 and skipped == 1  # study.log, progress.json, scorecard.png; the checkpoint waits
    assert (dst / "study.log").is_file() and (dst / "figures" / "scorecard.png").is_file()
    assert not (dst / "conv_ae" / "seed0" / "checkpoint.pt").exists()

    copied, nbytes, skipped = m.mirror_once(src, dst, slow_globs=["checkpoint.pt"], include_slow=True)
    assert copied == 1 and skipped == 0 and nbytes == 4096
    assert (dst / "conv_ae" / "seed0" / "checkpoint.pt").read_bytes() == b"\0" * 4096


def test_unchanged_files_are_not_recopied_and_changed_ones_are(tmp_path):
    m = _mod()
    src = _study_out(tmp_path / "out")
    dst = tmp_path / "drive"
    m.mirror_once(src, dst, slow_globs=[], include_slow=True)

    copied, _, _ = m.mirror_once(src, dst, slow_globs=[], include_slow=True)
    assert copied == 0

    log = src / "study.log"
    log.write_text("12:00:00 step 100\n12:03:00 step 200\n", encoding="utf-8")
    os.utime(log, (time.time() + 5, time.time() + 5))
    copied, _, _ = m.mirror_once(src, dst, slow_globs=[], include_slow=True)
    assert copied == 1
    assert "step 200" in (dst / "study.log").read_text(encoding="utf-8")


def test_half_written_files_are_never_mirrored(tmp_path):
    """The runner writes checkpoint.tmp then renames; a mirror must not copy the tmp."""
    m = _mod()
    src = tmp_path / "out"
    src.mkdir()
    (src / "checkpoint.tmp").write_bytes(b"half")
    (src / "results.json").write_text("{}", encoding="utf-8")
    dst = tmp_path / "drive"
    copied, _, _ = m.mirror_once(src, dst, slow_globs=[], include_slow=True)
    assert copied == 1 and not (dst / "checkpoint.tmp").exists()


def test_once_syncs_everything_through_the_cli(tmp_path, capsys):
    m = _mod()
    src = _study_out(tmp_path / "out")
    dst = tmp_path / "drive"
    assert m.main(["--src", str(src), "--dst", str(dst), "--once"]) == 0
    assert (dst / "conv_ae" / "seed0" / "checkpoint.pt").is_file()
    assert "final sync" in capsys.readouterr().out
