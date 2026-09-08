import json

from rl_researcher.presentation import document, hypothesis_view


def test_document_stays_in_project_and_rebases_links(tmp_path):
    root = tmp_path / "project"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "plan.md").write_text('# Plan\n\n[Report](report.md)\n\n![Chart](chart.png)\n\n<script>bad()</script>', encoding="utf-8")
    (tmp_path / "private.md").write_text("private", encoding="utf-8")
    code, data = document(root, "docs/plan.md")
    assert code == 200
    assert 'data-document="docs/report.md"' in data["html"]
    assert 'src="/docs/chart.png"' in data["html"]
    assert "<script>" not in data["html"]
    assert document(root, "../private.md")[0] == 404
    assert document(root, str(tmp_path / "private.md"))[0] == 404


def test_editorial_summary_never_rewrites_registration(tmp_path):
    question = 'Earlier work found a problem. Prediction: the model beats random. The control tests whether training helped.'
    assert hypothesis_view(tmp_path, "test", question)["question_summary"] == "the model beats random."
    (tmp_path / "research-ui.json").write_text(json.dumps({"test": {"question": "Does training improve planning?", "hypothesis": "Compare against random and untrained controls."}}), encoding="utf-8")
    view = hypothesis_view(tmp_path, "test", question)
    assert view["question_summary"] == "Does training improve planning?"
    assert view["hypothesis_summary"] == "Compare against random and untrained controls."
    assert question.startswith("Earlier work")


def test_invalid_editorial_file_falls_back_to_registration(tmp_path):
    (tmp_path / "research-ui.json").write_text("{broken", encoding="utf-8")
    assert hypothesis_view(tmp_path, "test", "A question.")["question_summary"] == "A question."
