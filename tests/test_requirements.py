from scripts.traceability import check, load_requirements


def test_register_vocabulary_and_honesty():
    """Register uses done|partial|pending|cut; cut/partial carry notes; done R-rows have tests."""
    assert check() == []
    ids = {str(row["id"]) for row in load_requirements()}
    assert "R-10" in ids
    assert {"D-01", "D-02", "D-03", "D-04", "D-05"} <= ids
