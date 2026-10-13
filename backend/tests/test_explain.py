"""test_explain.py - the Bedrock summary and, above all, its number guard. A stub stands in for Bedrock."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import explain as ex

client = TestClient(app)


def plan_of(**kw):
    return client.get("/api/plan", params=kw).json()


@pytest.fixture(scope="module")
def plan():
    return plan_of()


class Stub:
    def __init__(self, text=None, boom=False):
        self.text, self.boom, self.calls = text, boom, []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise RuntimeError("no credentials")
        return {"output": {"message": {"content": [{"text": self.text}]}}}


def good_text(facts):
    side = facts["intruders_out_of_100_getting_in_now"]["side_door"]
    return f"Today {side} of 100 simulated intruders reach the side door unseen. Turning C2 would help."


def test_facts_hold_only_whole_numbers_and_names(plan):
    f = ex.facts_from_plan(plan)
    assert f["kind"] == "property" and f["intruders_out_of_100_getting_in_now"]["side_door"] == 60
    assert "token" not in str(f).lower()


def test_a_correct_text_passes_the_guard(plan):
    f = ex.facts_from_plan(plan)
    assert ex.check_text(good_text(f), f) == (True, "")


def test_an_invented_number_is_rejected(plan):
    f = ex.facts_from_plan(plan)
    ok, why = ex.check_text("Today 61 of 100 intruders get in at the side door unseen.", f)
    assert not ok and "61" in why


def test_an_invented_camera_is_rejected(plan):
    f = ex.facts_from_plan(plan)
    ok, why = ex.check_text("Move camera C9 to the left so that more of the yard is covered here.", f)
    assert not ok and "C9" in why


def test_camera_names_do_not_count_as_numbers(plan):
    f = ex.facts_from_plan(plan, camera_names=["C1", "C2", "C3"])
    assert ex.check_text("Turning C2 helps a lot, and C1 and C3 can stay where they are.", f)[0]


def test_bedrock_text_is_used_when_it_passes(plan):
    f = ex.facts_from_plan(plan)
    stub = Stub(good_text(f))
    out = ex.explain(plan, client=stub, model_id="test-model")
    assert out["source"] == "bedrock" and out["text"].startswith("Today") and out["note"] is None
    sent = stub.calls[0]
    assert sent["modelId"] == "test-model" and sent["inferenceConfig"]["temperature"] == 0.0
    assert "facts" in sent["messages"][0]["content"][0]["text"].lower()


def test_a_wrong_number_falls_back_to_the_template(plan):
    out = ex.explain(plan, client=Stub("Today 77 of 100 simulated intruders reach the side door unseen."), model_id="m")
    assert out["source"] == "template" and out["text"] == ex.template_text(plan) and "rejected" in out["note"]


def test_a_failed_call_falls_back_quietly(plan):
    out = ex.explain(plan, client=Stub(boom=True), model_id="m")
    assert out["source"] == "template" and "could not be reached" in out["note"]


def test_not_configured_means_template(plan, monkeypatch):
    monkeypatch.delenv("REDHOUSE_BEDROCK_MODEL", raising=False)
    out = ex.explain(plan)
    assert out["source"] == "template" and out["note"] == "Bedrock is not configured."


def test_route_works_without_bedrock(monkeypatch):
    monkeypatch.delenv("REDHOUSE_BEDROCK_MODEL", raising=False)
    r = client.get("/api/explain")
    assert r.status_code == 200 and r.json()["source"] == "template" and len(r.json()["text"]) > 20


def test_route_uses_bedrock_when_configured_and_checks_it(monkeypatch):
    from app.routes import explain as route
    route._CACHE.clear()
    monkeypatch.setenv("REDHOUSE_BEDROCK_MODEL", "stub-model")
    monkeypatch.setattr(ex, "_bedrock_client", lambda: Stub("Seven hundred intruders get in. Add 12 cameras."))
    r = client.get("/api/explain").json()
    assert r["source"] == "template" and "rejected" in r["note"]
    route._CACHE.clear()


def test_indoor_facts_are_whole_numbers():
    import json
    plan = {"mode": "indoor", "best_existing": {"changes": [], "watched_after": 0.31, "sentence": "s"},
            "more_cameras": {"additions": [1, 2], "watched_after": 0.964, "sentence": "t"},
            "now": {"watched_frac": 0.31, "blind_m2": 136.9, "assets": [{"id": "server_room", "watched": False}]},
            "final": {"assets": [{"id": "server_room", "watched": True}]},
            "now_rates": [{"door": "server_room", "rate": 0.0}], "final_rates": [{"door": "server_room", "rate": 0.0}]}
    f = ex.facts_from_plan(plan)
    assert f["floor_watched_after_adding_pct"] == 96 and f["blind_area_m2"] == 137 and f["add_cameras"] == 2
    json.dumps(f)


class Seq(Stub):
    """Answers with each text in turn."""
    def __init__(self, *texts):
        super().__init__(texts[0])
        self.texts = list(texts)

    def converse(self, **kw):
        self.text = self.texts[min(len(self.calls), len(self.texts) - 1)]
        return super().converse(**kw)


def test_a_bad_first_answer_gets_one_retry_with_the_reason(plan):
    f = ex.facts_from_plan(plan)
    stub = Seq("All 77 doors are fine today, nothing to do here.", good_text(f))
    out = ex.explain(plan, client=stub, model_id="m")
    assert out["source"] == "bedrock" and len(stub.calls) == 2
    assert "rejected because" in stub.calls[1]["messages"][0]["content"][0]["text"]


def test_two_bad_answers_still_fall_back(plan):
    stub = Seq("There are 77 doors.", "There are 78 doors.")
    out = ex.explain(plan, client=stub, model_id="m")
    assert out["source"] == "template" and len(stub.calls) == 2 and "rejected" in out["note"]


def test_true_counts_are_allowed(plan):
    f = ex.facts_from_plan(plan, camera_names=["C1", "C2", "C3"])
    n = f["doors_or_spots_checked"]
    assert f["existing_camera_count"] == 3 and n >= 1
    assert ex.check_text(f"All {n} doors were checked and the 3 cameras are used.", f)[0]
