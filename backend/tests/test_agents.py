"""The 15 agricultural agents: contracts, arithmetic and refusals."""
from __future__ import annotations

import pytest

from app.agents import crop_data, registry
from app.agents.base import AgentContext
from app.core.schemas import Severity, Status
import reading_factory as simulator
from app.sensors.validation import validate
from app.services import weather as weather_service


async def build_context(question="", intent="general", scenario="normal", seed=5,
                        crop="wheat", sowing_date="2026-11-10", with_weather=True,
                        farm=None, provenance=""):
    from app.core.schemas import FarmContext
    farm = farm or FarmContext(crop=crop, sowing_date=sowing_date, area_acres=2.0)
    reading = simulator.reading(scenario, seed=seed, provenance=provenance)
    ctx = AgentContext(question=question, intent=intent, farm=farm,
                       entities={"crop": crop} if crop else {})
    if reading["reading"] is not None:
        payload = reading["reading"].model_dump(mode="json")
        if provenance:
            payload["provenance"] = provenance
        checked = validate(payload)
        ctx.reading = checked["reading"]
        ctx.reading_validation = checked["validation"]
    if with_weather:
        ctx.weather = await weather_service.get_summary(farm.latitude, farm.longitude)
    return ctx


# ------------------------------------------------------------- registry
def test_registry_holds_exactly_fifteen_agents():
    registry.assert_fifteen()
    assert sorted(registry.AGENTS) == list(range(1, 16))


def test_shared_capabilities_are_not_counted_as_agents():
    names = {a.name for a in registry.AGENTS.values()}
    for capability in registry.SHARED_CAPABILITIES:
        assert capability["name"] not in names
    assert {c["id"] for c in registry.SHARED_CAPABILITIES} >= {
        "conversational_ai", "llm", "rag", "orchestrator"}


def test_every_agent_declares_its_contract():
    for agent in registry.AGENTS.values():
        assert agent.purpose and agent.inputs and agent.outputs


def test_every_crop_the_advisor_offers_has_nutrient_figures():
    """Agent 6 needs a crop profile AND an NPK row, and adding a crop to one table
    without the other is a silent trap: the farmer's field says "tomato", the agent
    has the profile, and it still reports "needs an answer from you" on a question
    they already answered. Caught exactly that way."""
    from app.agents import crop_data
    from app.agents.environment import GENERAL_NPK_RECOMMENDATION

    profiles = set(crop_data.CROP_PROFILES)
    npk = set(GENERAL_NPK_RECOMMENDATION)
    assert profiles - npk == set(), "offered with no nutrient figures: %s" % sorted(profiles - npk)
    assert npk - profiles == set(), "nutrient figures for a crop nobody can pick: %s" % sorted(npk - profiles)


def test_every_day_of_every_crop_maps_to_a_stage():
    """No holes in any calendar.

    A day that belongs to no stage makes `stage_for_days` return None, and agent 4
    then asks the farmer how long ago they sowed - which is the one thing it already
    knows. Wheat had exactly this between day 8 and day 17: the fortnight after
    sowing, on the most grown crop here, when the farmer is out checking whether it
    has come up.
    """
    from app.agents import crop_data

    holes = {}
    for name, spec in crop_data.CROP_PROFILES.items():
        season_end = spec["stages"][-1]["end"]
        missing = [day for day in range(0, season_end + 1)
                   if crop_data.stage_for_days(name, day) is None]
        if missing:
            holes[name] = "days %d-%d" % (missing[0], missing[-1])
    assert holes == {}, "crop days with no stage: %s" % holes


def test_a_crop_calendar_covers_its_own_season_length():
    """The last stage has to reach the longest the crop is said to take.

    Otherwise a crop that runs late falls off the end of its own calendar, and the
    advice for the final weeks is whatever the last stage happened to be.
    """
    from app.agents import crop_data

    for name, spec in crop_data.CROP_PROFILES.items():
        longest = spec["duration_days"][1]
        assert spec["stages"][-1]["end"] == longest, (
            "%s: stages end at day %d but the crop is said to take up to %d"
            % (name, spec["stages"][-1]["end"], longest))
        assert spec["stages"][0]["start"] == 0, "%s does not start at day 0" % name


def test_a_season_fills_whole_rows_on_the_crop_advisor():
    """The advisor grid is four columns wide, two on a phone.

    A season whose crop count is not a multiple of four leaves holes in its last row.
    This is a layout rule living in a data test on purpose: the next crop added is
    what breaks it, and the person adding it is editing this table.
    """
    from app.agents import crop_data

    ragged = {}
    for season in ("rabi", "kharif", "zaid"):
        count = sum(1 for spec in crop_data.CROP_PROFILES.values()
                    if season in spec["seasons"])
        if count % 4:
            ragged[season] = "%d crops leaves %d empty card(s)" % (count, -count % 4)
    assert ragged == {}, ragged


@pytest.mark.asyncio
async def test_agent6_answers_for_a_crop_the_farmer_has_registered():
    ctx = await build_context("what fertiliser", intent="nutrient", crop="tomato")
    result = await registry.get(6).run(ctx)
    assert result.status is Status.OK, result.headline
    assert "tomato" in result.headline.lower() or "Tomato" in " ".join(result.findings)


@pytest.mark.asyncio
async def test_agent6_does_not_blame_the_farmer_for_a_gap_in_our_data():
    """Knowing the crop and holding no figures for it is OUR gap. Asking for the crop
    again would be unanswerable - no typing by the farmer can ever clear it."""
    ctx = await build_context("what fertiliser", intent="nutrient", crop="dragonfruit")
    result = await registry.get(6).run(ctx)
    assert "crop" not in [m.lower() for m in result.missing_inputs]
    assert not result.questions, "it must not ask for something already supplied"
    assert result.actions, "a dead end still needs somewhere to send the farmer"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", list(range(1, 16)))
async def test_every_agent_returns_a_valid_result(agent_id):
    ctx = await build_context("general farm question")
    result = await registry.get(agent_id).run(ctx)
    assert result.agent_id == agent_id
    assert result.status in set(Status)
    assert 0.0 <= result.confidence <= 1.0
    assert result.duration_ms >= 0


# ------------------------------------------------------------ agents 1-2
@pytest.mark.asyncio
async def test_agent1_reports_unmeasured_values_as_such():
    """A packet off a board with no probe wired in. The link is real, the numbers are
    generated, and the agent has to say so rather than report a measurement."""
    ctx = await build_context(scenario="normal", provenance="device_simulated")
    result = await registry.get(1).run(ctx)
    assert result.data["provenance"] == "device_simulated"
    assert any("not measurements" in w for w in result.warnings), result.warnings


@pytest.mark.asyncio
async def test_agent1_does_not_warn_about_a_measured_reading():
    ctx = await build_context(scenario="normal")
    result = await registry.get(1).run(ctx)
    assert result.data["provenance"] == "real_sensor"
    assert not any("not measurements" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_agent1_without_a_reading_asks_rather_than_assumes():
    ctx = AgentContext(question="what is my soil like")
    result = await registry.get(1).run(ctx)
    assert result.status is Status.NEEDS_INPUT
    assert result.missing_inputs


@pytest.mark.asyncio
async def test_agent2_flags_salinity_on_the_saline_scenario():
    ctx = await build_context(scenario="saline", seed=3)
    result = await registry.get(2).run(ctx)
    assert "salinity" in result.headline.lower() or any("EC" in f for f in result.findings)
    assert result.severity in (Severity.MEDIUM, Severity.HIGH)


@pytest.mark.asyncio
async def test_agent2_warns_that_sensor_npk_is_not_a_lab_test():
    ctx = await build_context(scenario="normal")
    result = await registry.get(2).run(ctx)
    assert any("laboratory" in w.lower() for w in result.warnings)


# -------------------------------------------------------------- agent 3
@pytest.mark.asyncio
async def test_agent3_ranks_crops_and_explains_each_score():
    from app.core.schemas import FarmContext
    ctx = await build_context(farm=FarmContext(season="rabi", crop=None, soil_type="alluvial"))
    result = await registry.get(3).run(ctx)
    ranked = result.data["ranked"]
    assert ranked and ranked == sorted(ranked, key=lambda r: -r["score"])
    assert all(r["reasons"] for r in ranked)


@pytest.mark.asyncio
async def test_agent3_penalises_crops_that_exceed_the_salinity_tolerance():
    ctx = await build_context(scenario="saline", seed=4)
    result = await registry.get(3).run(ctx)
    scores = {r["crop"]: r["score"] for r in result.data["ranked"]}
    assert scores.get("barley", 0) >= scores.get("chickpea", 0)


# -------------------------------------------------------------- agent 4
@pytest.mark.asyncio
async def test_agent4_derives_the_stage_from_the_sowing_date():
    from datetime import date, timedelta
    sown = (date.today() - timedelta(days=35)).isoformat()
    ctx = await build_context(sowing_date=sown)
    result = await registry.get(4).run(ctx)
    assert result.data["days_after_sowing"] == 35
    assert result.data["stage"] == "tillering"
    assert result.data["kc"] == crop_data.stage_by_name("wheat", "tillering")["kc"]


@pytest.mark.asyncio
async def test_agent4_asks_when_the_stage_cannot_be_established():
    ctx = await build_context(sowing_date=None)
    ctx.farm.stage = None
    result = await registry.get(4).run(ctx)
    assert result.status is Status.NEEDS_INPUT
    assert result.questions


# -------------------------------------------------------------- agent 6
@pytest.mark.asyncio
async def test_agent6_fertiliser_arithmetic_is_correct():
    ctx = await build_context(question="how much urea", intent="nutrient")
    result = await registry.get(6).run(ctx)
    products = result.data["products_kg_ha"]
    npk = result.data["recommended_npk_kg_ha"]
    # DAP supplies all the P2O5, urea supplies the balance of N, MOP the K2O.
    assert round(products["dap"] * 0.46) == npk["P2O5"]
    assert round(products["mop"] * 0.60) == npk["K2O"]
    n_total = products["dap"] * 0.18 + products["urea"] * 0.46
    assert abs(n_total - npk["N"]) < 1.0


@pytest.mark.asyncio
async def test_agent6_refuses_to_prescribe_from_sensor_npk_alone():
    ctx = await build_context(intent="nutrient")
    result = await registry.get(6).run(ctx)
    assert any("NOT calculated from the sensor" in w for w in result.warnings)
    assert any("soil test" in w.lower() for w in result.warnings)


# -------------------------------------------------------------- agent 7
@pytest.mark.asyncio
async def test_agent7_recommends_irrigation_when_the_soil_is_dry():
    from datetime import date, timedelta
    sown = (date.today() - timedelta(days=35)).isoformat()
    ctx = await build_context(scenario="dry", seed=7, sowing_date=sown)
    ctx.results[4] = await registry.get(4).run(ctx)
    result = await registry.get(7).run(ctx)
    assert result.data["decision"].startswith("irrigate")
    assert result.data["gross_depth_mm"] > result.data["net_depth_mm"]


@pytest.mark.asyncio
async def test_agent7_stops_irrigation_at_maturity():
    from datetime import date, timedelta
    sown = (date.today() - timedelta(days=130)).isoformat()
    ctx = await build_context(scenario="dry", seed=7, sowing_date=sown)
    ctx.results[4] = await registry.get(4).run(ctx)
    result = await registry.get(7).run(ctx)
    assert "stop irrigation" in result.data["decision"]


@pytest.mark.asyncio
async def test_agent7_states_its_assumptions():
    ctx = await build_context(scenario="dry", seed=7)
    result = await registry.get(7).run(ctx)
    assert any("root depth" in a.lower() for a in result.assumptions)
    assert any("efficiency" in a.lower() for a in result.assumptions)


# ------------------------------------------------------------ agents 8-11
@pytest.mark.asyncio
async def test_agent8_never_gives_a_definitive_diagnosis():
    ctx = await build_context("my wheat has yellow leaves with spots", intent="pest_disease")
    result = await registry.get(8).run(ctx)

    # The guarantee is about the CONTENT, not the status code. With a symptom the agent
    # has done its job and reports OK - it names the candidates and the checks that
    # separate them. What it must never do is settle on one cause or name a treatment,
    # and it must still say the field check comes first.
    assert len(result.data["candidate_causes"]) > 1, "a single cause reads as a diagnosis"
    assert any("not a diagnosis" in f.lower() for f in result.findings)
    assert result.questions, "it must still be asking what would narrow this down"
    assert result.warnings
    assert "confirm" in result.headline.lower()
    assert result.confidence < 0.75, "a symptom description cannot support high confidence"


@pytest.mark.asyncio
async def test_agent8_asks_for_a_description_when_none_is_given():
    ctx = await build_context("something is wrong with my crop", intent="pest_disease")
    result = await registry.get(8).run(ctx)
    assert result.status in (Status.NEEDS_INPUT, Status.UNAVAILABLE)
    assert result.questions


@pytest.mark.asyncio
async def test_agent11_refuses_to_give_a_dose():
    ctx = await build_context("what dose of pesticide should I spray per acre",
                              intent="crop_protection")
    result = await registry.get(11).run(ctx)
    assert result.data["dose_requested"] is True
    assert result.severity is Severity.HIGH
    assert any("label" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_agent9_offers_non_chemical_options_first():
    ctx = await build_context("weeds in my wheat", intent="weed")
    result = await registry.get(9).run(ctx)
    assert "cultural" in result.actions[0].lower()
    assert any("resistance" in w.lower() for w in result.warnings)


# ----------------------------------------------------------- agents 12-14
@pytest.mark.asyncio
async def test_agent12_reports_prices_with_source_and_date():
    from app.services import market as market_service
    ctx = await build_context("wheat price", intent="market")
    ctx.market = await market_service.get_summary("wheat", "Uttar Pradesh")
    result = await registry.get(12).run(ctx)
    assert result.data["unit"] == "INR per quintal"
    assert any("source" in f.lower() or "records" in f.lower() for f in result.findings)
    assert any("do not predict" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_agent12_says_so_when_prices_are_unavailable():
    ctx = await build_context("wheat price", intent="market")
    ctx.market = {"available": False, "reason": "key missing"}
    result = await registry.get(12).run(ctx)
    assert result.status is Status.UNAVAILABLE
    assert "unavailable" in result.headline.lower()


@pytest.mark.asyncio
async def test_agent13_computes_break_even_from_the_farmers_own_numbers():
    ctx = await build_context("my cost was 24000 and I expect 20 quintal", intent="economics")
    result = await registry.get(13).run(ctx)
    assert result.data["break_even_price"] == pytest.approx(1200.0)
    assert any("not hold input prices" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_agent14_never_quantifies_an_environmental_claim():
    ctx = await build_context("what to do with the straw", intent="residue")
    result = await registry.get(14).run(ctx)
    assert len(result.data["options"]) >= 4
    assert any("measurement" in w.lower() for w in result.warnings)


# -------------------------------------------------------------- agent 15
@pytest.mark.asyncio
async def test_agent15_flags_unmeasured_data_and_counts_provenance():
    """Agent 15 exists to stop an unmeasured number being read as a measured one."""
    ctx = await build_context("should I irrigate", intent="irrigation",
                              provenance="device_simulated")
    for agent_id in (1, 2, 4, 5, 7):
        ctx.results[agent_id] = await registry.get(agent_id).run(ctx)
    result = await registry.get(15).run(ctx)
    assert result.data["simulated_data_used"] is True
    assert any("not measurements" in w.lower() or "simulated" in w.lower()
               for w in result.warnings), result.warnings
    assert result.data["agents_checked"] == 5


@pytest.mark.asyncio
async def test_agent15_detects_the_irrigation_versus_maturity_conflict():
    from app.core.schemas import AgentResult
    ctx = await build_context("should I irrigate", intent="irrigation")
    ctx.results[4] = AgentResult(agent_id=4, agent_name="stage", data={"stage": "maturity"})
    ctx.results[7] = AgentResult(agent_id=7, agent_name="irrigation",
                                 data={"decision": "irrigate now"})
    result = await registry.get(15).run(ctx)
    assert result.data["conflicts_found"] == 1
    assert any("maturity" in f for f in result.findings)


@pytest.mark.asyncio
async def test_agent15_escalates_when_a_dose_was_requested():
    ctx = await build_context("what dose per acre", intent="crop_protection")
    for agent_id in (8, 11):
        ctx.results[agent_id] = await registry.get(agent_id).run(ctx)
    result = await registry.get(15).run(ctx)
    assert result.data["escalation_required"] is True
    assert any("officer" in a.lower() for a in result.actions)
