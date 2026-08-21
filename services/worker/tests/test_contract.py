from app.llm.base import StructuredRequest


def test_request_contract():
    assert (
        StructuredRequest(
            task="technical",
            evidence_refs=["e1"],
            prompt_version="v1",
            max_cost_micros=100,
        ).max_cost_micros
        == 100
    )
