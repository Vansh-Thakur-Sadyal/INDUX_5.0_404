"""Answering the chain, and stopping it.

An agent that will not guess asks a question instead. That is the right behaviour and
it is why Crop Growth Stage reports "needs input" rather than inventing a sowing date.
What was missing was anywhere to put the answer: the run accepted no inputs, so the
same three agents stalled on every run and the farmer could only watch.
"""
from __future__ import annotations

import pytest

from app.routers import pipeline as pipeline_router


def _run(client, **payload):
    response = client.post("/api/pipeline/run", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _by_id(body, agent_id):
    for result in body["agent_results"]:
        if result["agent_id"] == agent_id:
            return result
    raise AssertionError("agent %d did not run" % agent_id)


# ------------------------------------------------------- what the page should ask
def test_a_stalled_run_says_exactly_what_to_type(client):
    body = _run(client, question="check my farm")
    needed = {i["key"]: i for i in body["summary"]["needed_inputs"]}
    assert needed, "the run stalled and asked for nothing the page could render"
    for entry in needed.values():
        # "select" and "multiselect" carry an `options` list, so the farmer
        # picks rather than types - a misspelt crop reaches the agents as no
        # crop at all.
        assert entry["label"]
        assert entry["type"] in ("text", "number", "date", "select", "multiselect")
        assert entry["asked_by"], "an input nobody asked for would be noise"


def test_nothing_is_asked_for_an_agent_that_answered(client):
    """Only agents that actually stalled put a box on the page."""
    body = _run(client, question="check my farm")
    stalled = {r["agent_id"] for r in body["agent_results"]
               if r["status"] in ("needs_input", "unavailable")}
    for entry in body["summary"]["needed_inputs"]:
        owners = [a for a, keys in pipeline_router.AGENT_INPUTS.items()
                  if entry["key"] in keys]
        assert stalled & set(owners), "%s was asked for by no stalled agent" % entry["key"]


# --------------------------------------------------------- the answers land
def test_a_sowing_date_completes_the_crop_stage_agent(client):
    """Agent 4 at 0% on every run was the complaint."""
    from datetime import date, timedelta
    sown = (date.today() - timedelta(days=45)).isoformat()

    before = _by_id(_run(client, question="check my farm"), 4)
    assert before["status"] in ("needs_input", "unavailable")

    after = _by_id(_run(client, question="check my farm",
                        answers={"crop": "wheat", "sowing_date": sown}), 4)
    assert after["status"] == "ok", after.get("headline")
    assert "45 days" in " ".join(after["findings"]) or "stage" in after["headline"].lower()


def test_a_reported_stage_works_when_the_date_is_not_known(client):
    after = _by_id(_run(client, question="check my farm",
                        answers={"crop": "wheat", "stage": "flowering"}), 4)
    assert after["status"] == "ok"


def test_a_symptom_description_gets_the_pest_agent_to_name_causes(client):
    """It does NOT go green, and should not.

    With a symptom it moves from "I cannot say anything" to a list of candidate causes
    plus the checks that would tell them apart - and it stays at needs_input because
    naming a pesticide off a one-line description, unconfirmed in the field, is the
    thing this agent exists not to do. The improvement to assert is the findings.
    """
    before = _by_id(_run(client, question="check my farm"), 8)
    assert before["status"] in ("needs_input", "unavailable")
    assert not before["findings"], "it had nothing to say before, by design"

    after = _by_id(_run(client, question="check my farm",
                        answers={"crop": "wheat",
                                 "symptoms": "yellowing on the lower leaves, 4 plants in 10"}), 8)
    assert after["findings"], "the symptom reached the agent but produced nothing"
    assert "cause" in after["headline"].lower()
    assert after["actions"], "candidate causes with no next check are not usable"


def test_cost_and_yield_complete_the_economics_agent(client):
    """It read the question text only, so the box it asked for had nowhere to go."""
    after = _by_id(_run(client, question="check my farm",
                        answers={"cost": "42000", "yield_quintal": "18"}), 13)
    assert after["status"] == "ok"
    assert after["data"].get("break_even_price") == pytest.approx(42000 / 18, rel=0.01)


def test_the_answers_come_back_so_the_page_can_keep_them(client):
    body = _run(client, question="check my farm",
                answers={"cost": "42000", "crop": "wheat"})
    assert body["applied_answers"]["entities"]["cost"] == 42000.0
    assert body["applied_answers"]["farm"]["crop"] == "wheat"


def test_a_blank_answer_is_ignored_rather_than_stored_as_empty(client):
    body = _run(client, question="check my farm",
                answers={"cost": "", "crop": "   ", "yield_quintal": "12"})
    assert "cost" not in body["applied_answers"]["entities"]
    assert "crop" not in body["applied_answers"]["farm"]
    assert body["applied_answers"]["entities"]["yield_quintal"] == 12.0


def test_an_unknown_answer_key_cannot_reach_the_farm_record(client):
    body = _run(client, question="check my farm",
                answers={"village": "nowhere", "role": "admin"})
    assert body["applied_answers"]["farm"] == {}
    assert body["applied_answers"]["entities"] == {}


def test_answering_shrinks_the_list_of_things_still_missing(client):
    from datetime import date, timedelta
    sown = (date.today() - timedelta(days=45)).isoformat()
    before = _run(client, question="check my farm")["summary"]
    after = _run(client, question="check my farm",
                 answers={"crop": "wheat", "sowing_date": sown, "cost": "42000",
                          "yield_quintal": "18",
                          "symptoms": "yellowing on lower leaves"})["summary"]
    assert after["needs_input"] + after["unavailable"] < before["needs_input"] + before["unavailable"]
    assert after["ok"] > before["ok"]


# ------------------------------------------- the form before anything has run
def test_the_labels_are_available_before_any_run(client):
    """The first form a farmer sees is the one they most need telling about, and it
    used to carry raw field names because the labels only arrived with a finished run."""
    body = client.get("/api/pipeline/inputs").json()
    keys = {i["key"] for i in body["inputs"]}
    assert {"crop", "sowing_date", "symptoms", "cost"} <= keys
    for item in body["inputs"]:
        assert item["label"] and not item["label"].islower(), item["key"]
        assert item["type"] in ("text", "number", "date", "select", "multiselect")
        if item["type"] in ("select", "multiselect"):
            assert item.get("options"), f"{item['key']} offers a picker with nothing in it"
        assert item["placeholder"] or item.get("help"), item["key"]


def test_a_date_is_typed_as_a_date(client):
    """So the browser offers a calendar instead of asking a farmer to format a date."""
    spec = next(i for i in client.get("/api/pipeline/inputs").json()["inputs"]
                if i["key"] == "sowing_date")
    assert spec["type"] == "date"


def test_every_input_is_wanted_by_some_agent(client):
    """A box nobody reads is a box nobody should be asked to fill."""
    body = client.get("/api/pipeline/inputs").json()
    wanted = {k for keys in body["asked_by"].values() for k in keys}
    for item in body["inputs"]:
        assert item["key"] in wanted, "%s is asked for by no agent" % item["key"]


# ----------------------------------------------------------------- stopping
def test_stopping_a_run_that_is_not_running_says_so(client):
    body = client.post("/api/pipeline/stop", json={"run_token": "nope"}).json()
    assert body["stopped"] is False
    assert "nothing to stop" in body["note"]


def test_the_status_endpoint_reports_the_deadline(client):
    body = client.get("/api/pipeline/status").json()
    assert body["timeout_seconds"] == pipeline_router.PIPELINE_TIMEOUT_SECONDS
    assert body["running"] == 0


@pytest.mark.asyncio
async def test_a_run_in_flight_can_be_cancelled():
    """The browser aborting its own fetch frees the page but leaves the work running
    on the server, holding a model call open. Stop has to cancel the task itself."""
    import asyncio

    async def forever():
        await asyncio.sleep(30)

    task = asyncio.ensure_future(forever())
    pipeline_router._running["tok"] = task
    try:
        body = await pipeline_router.stop_pipeline({"run_token": "tok"})
        assert body["stopped"] is True
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
    finally:
        task.cancel()
        pipeline_router._running.pop("tok", None)
