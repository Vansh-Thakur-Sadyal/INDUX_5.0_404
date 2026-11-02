"""What the assistant is allowed to do to the page, and what it takes.

The rule the whole thing rests on: **the model proposes, the policy engine
decides, and the browser executes exactly one validated thing.** The model never
returns code, never names a selector and never reaches the page.

So the tests are mostly about refusals, and about the one thing a confirmation
has to be: unforgeable. "The user agreed", sent by the browser, is worth nothing
- the browser is the thing being driven.
"""
from __future__ import annotations

import pytest

from app.conversation import tools


@pytest.fixture(autouse=True)
def _clear_pending():
    tools._pending.clear()
    yield
    tools._pending.clear()


# -------------------------------------------------------------- read is free
def test_a_read_only_action_runs_on_its_own():
    decision = tools.decide({"tool": "get_page_summary", "arguments": {}})
    assert decision.allowed is True
    assert decision.tier == tools.READ


def test_scrolling_needs_an_id_that_came_from_a_snapshot():
    good = tools.decide({"tool": "scroll_to", "arguments": {"targetId": "el-42"}})
    assert good.allowed is True

    for bad in ("main > .card:nth-child(2)", "el-", "", None, "../admin", "el-99999999"):
        decision = tools.decide({"tool": "scroll_to", "arguments": {"targetId": bad}})
        assert decision.allowed is False, bad
        assert "element id" in decision.reason


# ------------------------------------------------- low risk needs permission
def test_a_low_risk_action_waits_until_actions_are_switched_on():
    proposal = {"tool": "navigate", "arguments": {"page": "market"}}

    off = tools.decide(proposal)
    assert off.allowed is False
    assert "switched on" in off.reason

    on = tools.decide(proposal, allow_low_risk=True)
    assert on.allowed is True


def test_navigation_is_an_allowlist_not_an_origin_check():
    """"Same origin" would still let a model send a farmer to the admin
    console. Only the dashboard's own pages are reachable, by name."""
    for page in ("admin", "knowledge", "https://example.com", "../admin", ""):
        decision = tools.decide({"tool": "navigate", "arguments": {"page": page}},
                                allow_low_risk=True)
        assert decision.allowed is False, page

    assert tools.decide({"tool": "navigate", "arguments": {"page": "soil"}},
                        allow_low_risk=True).allowed is True


# ------------------------------------------------------- the model inventing
def test_a_tool_that_does_not_exist_is_named_and_refused():
    decision = tools.decide({"tool": "run_javascript",
                             "arguments": {"code": "alert(1)"}})
    assert decision.allowed is False
    assert "no tool called" in decision.reason
    assert decision.tool == "run_javascript"


def test_arguments_the_tool_never_declared_are_dropped():
    decision = tools.decide({"tool": "scroll_to",
                             "arguments": {"targetId": "el-7", "script": "alert(1)",
                                           "url": "https://example.com"}})
    assert decision.allowed is True
    assert decision.arguments == {"targetId": "el-7"}


def test_a_proposal_that_is_not_an_object_is_refused():
    for junk in ("scroll_to", 42, None, ["scroll_to"]):
        assert tools.decide(junk).allowed is False


def test_bad_arguments_are_refused_with_the_reason():
    decision = tools.decide({"tool": "scroll_to",
                             "arguments": {"targetId": "the green button"}})
    assert decision.allowed is False
    assert "targetId" in decision.reason

    # An id this page never issued, and a page that is not one of ours.
    assert tools.decide({"tool": "scroll_to",
                         "arguments": {"targetId": "el-9999999"}}).allowed is False
    assert tools.decide({"tool": "navigate",
                         "arguments": {"page": "admin-console"}}).allowed is False
    assert tools.decide({"tool": "scroll_to",
                         "arguments": {"targetId": "el-7"}}).allowed is True


# ------------------------------------------------------- consequential tiers
def test_a_consequential_action_is_held_until_it_is_confirmed(monkeypatch):
    """Nothing in the registry is consequential today, which is deliberate -
    this assistant has no tool that spends money or sends a message. The tier
    is tested with one injected, so that adding a real one cannot be the same
    thing as shipping it unguarded."""
    monkeypatch.setitem(tools.REGISTRY, "send_message", tools.Tool(
        name="send_message", tier=tools.CONSEQUENTIAL,
        summary="Send a message", arguments={"to": "who"},
        validate=lambda args: None if args.get("to") else "to is required",
        consequence="send a message to Ramesh"))

    held = tools.decide({"tool": "send_message", "arguments": {"to": "Ramesh"}},
                        owner="u1", allow_low_risk=True)
    assert held.allowed is False, "a consequential action ran without a yes"
    assert held.confirm_id
    assert held.consequence == "send a message to Ramesh"

    released = tools.confirm(held.confirm_id, owner="u1")
    assert released.allowed is True
    assert released.tool == "send_message"
    assert released.arguments == {"to": "Ramesh"}


def test_a_confirmation_is_good_once(monkeypatch):
    monkeypatch.setitem(tools.REGISTRY, "send_message", tools.Tool(
        name="send_message", tier=tools.CONSEQUENTIAL, summary="Send",
        arguments={"to": "who"}, validate=lambda a: None))
    held = tools.decide({"tool": "send_message", "arguments": {"to": "x"}}, owner="u1")

    assert tools.confirm(held.confirm_id, owner="u1").allowed is True
    again = tools.confirm(held.confirm_id, owner="u1")
    assert again.allowed is False
    assert "expired or was already used" in again.reason


def test_a_confirmation_belongs_to_the_session_it_was_issued_to(monkeypatch):
    monkeypatch.setitem(tools.REGISTRY, "send_message", tools.Tool(
        name="send_message", tier=tools.CONSEQUENTIAL, summary="Send",
        arguments={"to": "who"}, validate=lambda a: None))
    held = tools.decide({"tool": "send_message", "arguments": {"to": "x"}}, owner="u1")

    stolen = tools.confirm(held.confirm_id, owner="u2")
    assert stolen.allowed is False
    assert "another session" in stolen.reason


def test_an_invented_confirmation_id_releases_nothing():
    assert tools.confirm("act-deadbeefdead").allowed is False
    assert tools.confirm("").allowed is False


def test_a_confirmation_does_not_outlive_the_page(monkeypatch):
    monkeypatch.setitem(tools.REGISTRY, "send_message", tools.Tool(
        name="send_message", tier=tools.CONSEQUENTIAL, summary="Send",
        arguments={"to": "who"}, validate=lambda a: None))
    held = tools.decide({"tool": "send_message", "arguments": {"to": "x"}}, owner="u1")

    # Wind the clock past the window rather than sleeping through it.
    tools._pending[held.confirm_id]["at"] -= tools.PENDING_TTL_SECONDS + 1
    assert tools.confirm(held.confirm_id, owner="u1").allowed is False


# ------------------------------------------------------------- the catalogue
def test_the_catalogue_and_the_prompt_come_from_the_same_registry():
    """So the widget, the prompt and the documentation cannot drift apart."""
    catalogue = {entry["tool"] for entry in tools.catalogue()}
    assert catalogue == set(tools.REGISTRY)

    block = tools.prompt_block()
    for name in tools.REGISTRY:
        assert name in block
    assert "Never emit code" in block


def test_the_endpoint_serves_the_same_tools(client):
    body = client.get("/api/assistant/tools").json()
    assert {t["tool"] for t in body["tools"]} == set(tools.REGISTRY)
    assert set(body["tiers"]) == {"read", "low", "consequential"}
