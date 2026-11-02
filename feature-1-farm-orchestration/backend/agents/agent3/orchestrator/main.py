# ==========================================
# AGENT 3 (TRUE AGENTIC AI - FULL LLM CONTROL)
# ==========================================

import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

try:
    import progress
except ImportError:
    progress = None

from planner.agent import (
    ConstraintOptimizationAgent,
    RiskSignal,
    WeatherWindow,
    FieldConstraints
)

from executor.agents.executor_agent import executor_agent
from monitoring.feedback_agent import monitoring_agent
from memory_agent import learn_from_run


# ==========================================
# 🧠 LLM DECISION ENGINE (CORE UPGRADE)
# ==========================================

def llm_decision_engine(agent2_output):

    try:
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
        from llm_client import mistral_chat

        output = mistral_chat(
            [{
                "role": "user",
                "content": f"""
                You are an intelligent farm decision AI.

                Input Data:
                {agent2_output}

                Decide:
                1. Best strategy (optimize, low_cost, high_yield, risk_avoid)
                2. Budget (number)
                3. Water usage (number)
                4. Priority (cost / yield / risk / balanced)

                Return JSON only like:
                {{
                  "strategy": "...",
                  "budget": ...,
                  "water": ...,
                  "priority": "...",
                  "reason": "..."
                }}

                "reason" MUST be one short plain-text sentence (a string,
                never an object or list).
                """
            }],
            json_mode=True,
        )

        import json

        # 🔥 Clean LLM output
        start = output.find("{")
        end = output.rfind("}") + 1
        parsed = json.loads(output[start:end])

        return parsed

    except Exception as e:
        print("LLM failed → fallback:", e)

        return {
            "strategy": "optimize",
            "budget": 10000,
            "water": 10000,
            "priority": "balanced",
            "reason": "fallback"
        }


# ==========================================
# CORE SYSTEM
# ==========================================

def run_system(agent2_output, ui_input=None):

    print("\n🚀 AGENT 3 (TRUE AI) STARTED\n")

    ui = ui_input if isinstance(ui_input, dict) else {}
    ui_agent3 = ui.get("agent3") if isinstance(ui.get("agent3"), dict) else {}
    ui_agent4 = ui.get("agent4") if isinstance(ui.get("agent4"), dict) else {}

    agent_data = agent2_output.get("agent2_output", {})
    input_used = agent2_output.get("input_used", {})

    risk_score = agent_data.get("risk_score", 0)

    # =============================
    # RISKS
    # =============================

    # Normalize Agent 2 risk names ("Water Stress", "Pest Probability"...)
    # to the planner's risk types so each risk gets a matching action.
    def normalize_risk(name):
        n = str(name).lower()
        if "water" in n:
            return "water_stress"
        if "temperature" in n or "heat" in n:
            return "heat_stress"
        if "pest" in n:
            return "pest"
        if "nutrient" in n:
            return "nutrient_gap"
        if "disease" in n:
            return "disease"
        return "anomaly"

    # Risks arrive as (name, level, severity, confidence) — severity and
    # confidence are computed by Agent 2's detectors from the actual data.
    risks = [
        RiskSignal(
            risk_type=normalize_risk(r[0]),
            severity=float(r[2]) if len(r) > 2 else (1.0 if r[1] == "HIGH" else 0.5),
            affected_field_id="field_1",
            confidence=float(r[3]) if len(r) > 3 else 0.8
        )
        for r in agent_data.get("risks", [])
    ]

    if not risks:
        risks = [RiskSignal("low_risk", 0.1, "field_1", 0.8)]

    # =============================
    # SENSOR DATA
    # =============================

    sensor_data = {
        "soil_moisture": input_used.get("soil_moisture", 50),
        "temperature": input_used.get("temperature", 30),
        "air_temp": input_used.get("temperature", 30),
        "humidity": input_used.get("humidity", 60),
        "nitrogen": 40,
        "phosphorus": 30,
        "potassium": 35,
        "soil_ph": 6.5
    }

    # =============================
    # 🧠 FULL LLM DECISION
    # =============================

    decision = llm_decision_engine(agent2_output)

    # ── Dashboard inputs override the LLM's guesses ──
    # Strategy button and budget/water sliders come from the UI;
    # the LLM only fills in priority and the reasoning text.
    strategy_map = {
        "Optimize Resources": "optimize",
        "High Yield Focus": "high_yield",
        "Sustainable Loop": "low_cost",
    }
    ui_strategy = strategy_map.get(ui_agent3.get("strategy"))

    def _num(value):
        try:
            n = float(value)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    strategy = ui_strategy or decision.get("strategy", "optimize")
    budget = _num(ui_agent3.get("budget")) or _num(decision.get("budget")) or 10000
    water = _num(ui_agent3.get("water")) or _num(decision.get("water")) or 10000
    priority = decision.get("priority", "balanced")

    # Reason must be a plain string for the dashboard
    reason = decision.get("reason", "")
    if not isinstance(reason, str):
        import json as _json
        reason = _json.dumps(reason)[:300]

    print("🧠 LLM Decision:", decision)
    print(f"🎛️ Using UI constraints → strategy: {strategy}, budget: {budget}, water: {water}")

    # ── Strategy changes the actual plan ──
    # high_yield: proactively fertilize for yield on top of risk actions
    # low_cost (Sustainable Loop): only spend up to 40% of the limits
    plan_budget, plan_water = budget, water

    if strategy == "high_yield":
        risks.append(RiskSignal("nutrient_gap", 0.6, "field_1", 0.8))
    elif strategy == "low_cost":
        plan_budget = budget * 0.4
        plan_water = water * 0.4
        print(f"🌱 Sustainable strategy → plan capped at ₹{plan_budget:.0f} / {plan_water:.0f}L")

    # =============================
    # WEATHER + CONSTRAINTS
    # =============================

    weather_windows = [
        WeatherWindow(
            field_id="field_1",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(hours=6),
            wind_speed_kmh=10,
            rain_probability=0.2,
            temperature_c=sensor_data["temperature"],
            is_spray_safe=True,
            is_irrigation_needed=True
        )
    ]

    constraints = FieldConstraints(
        field_id="field_1",
        total_budget_inr=plan_budget,
        max_water_liters=plan_water,
        crop_stage="vegetative",
        area_hectares=1.0
    )

    # =============================
    # PLANNER
    # =============================

    planner = ConstraintOptimizationAgent()
    plan = planner.run(risks, weather_windows, constraints)
    plan_dict = plan.to_dict()

    # Normalize risk reduction against the TOTAL detected risk, so the
    # value reflects how much of the risk the plan actually covers
    # instead of always capping at 100%.
    total_severity = sum(r.severity for r in risks) or 1.0
    covered = sum(a.effectiveness_score for a in plan.selected_actions)
    plan_dict["expected_risk_reduction"] = round(min(covered / total_severity, 0.95), 3)

    # =============================
    # EXECUTOR
    # =============================

    actions = [a["type"] for a in plan_dict["actions"]]
    execution_result = executor_agent(actions, sensor_data)

    # =============================
    # MONITORING
    # =============================

    if progress:
        progress.set_step(4, "Agent 4: Monitoring sync")

    feedback = monitoring_agent(
        data={
            "sensor_data": sensor_data,
            "weather": {"rain_forecast": 0.2}
        },
        risks=[r.risk_type for r in risks],
        plan=plan_dict,
        actions=execution_result,
        alert_level=ui_agent4.get("alert_level", 50)
    )

    # =============================
    # 🧠 MEMORY LEARNING
    # =============================

    if progress:
        progress.set_step(5, "Agent 5: Memory calibration")

    learning = learn_from_run({
        "strategy": strategy,
        "budget": budget,
        "water": water,
        "risk_score": risk_score,
        "plan": plan_dict,
        "actions": execution_result
    })

    # =============================
    # FINAL OUTPUT
    # =============================

    return {
        "agent": "DecisionAgent",
        "strategy_used": strategy,
        "priority": priority,
        "budget": budget,
        "water": water,
        "llm_reason": reason,
        "risk_score": risk_score,
        "plan": plan_dict,
        "actions_executed": execution_result,
        "monitoring_feedback": feedback,
        "learning": learning,
        "status": "OPTIMIZED"
    }


# ==========================================
# ENTRY
# ==========================================

def main(agent2_output, ui_input=None):
    return run_system(agent2_output, ui_input)
