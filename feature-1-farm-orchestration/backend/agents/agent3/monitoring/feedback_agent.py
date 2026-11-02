# ==========================================
# AGENT 4 (INTELLIGENT MONITORING + LLM CORE)
# ==========================================

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

# GLOBAL MEMORY
history = []
performance_log = []

# ==========================================
# 🧠 LLM MONITORING CORE
# ==========================================

def llm_monitoring_analysis(data, risks, plan, actions, context):

    try:
        from llm_client import mistral_chat

        return mistral_chat([{
            "role": "user",
            "content": f"""
            You are an intelligent farm monitoring AI.

            SENSOR DATA:
            {data}

            RISKS:
            {risks}

            PLAN:
            {plan}

            ACTIONS:
            {actions}

            CONTEXT:
            {context}

            Provide:
            - system status (normal / alert)
            - key problems
            - suggestions
            - improvements
            - risk insights

            Keep it short and practical.
            """
        }])

    except:
        return None


# ==========================================
# CORE MONITORING AGENT
# ==========================================

def monitoring_agent(data, risks, plan, actions, alert_level=50):

    global history, performance_log

    # =============================
    # INPUT EXTRACTION
    # =============================

    soil = data["sensor_data"]["soil_moisture"]
    temp = data["sensor_data"]["temperature"]
    rain = data["weather"]["rain_forecast"]

    # Alert-level slider from the dashboard controls escalation:
    # high = escalate on the first failure, low = never escalate.
    try:
        alert_level = float(alert_level)
    except (TypeError, ValueError):
        alert_level = 50

    status = "normal"
    feedback = []
    # plan is a dict — collect adjustments separately instead of
    # appending to it (appending crashed once trend analysis kicked in
    # after a few runs).
    adjustments = []
    escalation = False

    # =============================
    # 1️⃣ BASIC DETECTION (fallback)
    # =============================

    if "water_stress" in risks:
        feedback.append("Low soil moisture detected")
        status = "alert"

    if "heat_stress" in risks:
        feedback.append("High temperature detected")
        status = "alert"

    if not feedback:
        feedback.append("Conditions are within optimal range")

    # =============================
    # 2️⃣ TREND ANALYSIS
    # =============================

    trend = "no_data"

    if len(history) > 0:
        last = history[-1]

        if soil > last["soil"]:
            trend = "improving"
        elif soil < last["soil"]:
            trend = "worsening"
        else:
            trend = "stable"

    # =============================
    # 3️⃣ PERFORMANCE LOGIC
    # =============================

    if trend == "improving":
        performance = "success"

    elif trend == "worsening":

        if rain:
            performance = "environmental"
            feedback.append("Rain affecting soil moisture")

        elif temp > 38:
            performance = "environmental"
            feedback.append("Heat impacting soil moisture")

        else:
            performance = "failure"

    else:
        performance = "stable"

    performance_log.append(performance)

    # =============================
    # 4️⃣ SMART PLAN UPDATE
    # =============================

    if performance == "failure":
        adjustments.append("Increase irrigation duration")
        feedback.append("Plan ineffective → increasing irrigation")

    elif performance == "environmental":
        adjustments.append("Adjust irrigation timing")
        feedback.append("Adjusting for environmental impact")

    elif performance == "success":
        feedback.append("Plan working well")

    # =============================
    # 5️⃣ ESCALATION LOGIC
    # =============================

    recent_failures = [p for p in performance_log[-3:] if p == "failure"]

    # Escalation threshold set by the dashboard alert-level slider
    if alert_level >= 70:
        failure_threshold = 1     # aggressive: any failure escalates
    elif alert_level >= 30:
        failure_threshold = 2     # standard
    else:
        failure_threshold = 99    # relaxed: never escalate

    if len(recent_failures) >= failure_threshold:
        escalation = True
        feedback.append("Escalate to expert")

    # =============================
    # 🧠 6️⃣ LLM INTELLIGENCE (MAIN)
    # =============================

    context = {
        "trend": trend,
        "performance": performance,
        "history_length": len(history)
    }

    llm_insight = llm_monitoring_analysis(
        data=data,
        risks=risks,
        plan=plan,
        actions=actions,
        context=context
    )

    # =============================
    # 7️⃣ MEMORY UPDATE
    # =============================

    history.append({
        "soil": soil,
        "temperature": temp,
        "trend": trend,
        "performance": performance
    })

    # =============================
    # 8️⃣ FINAL RESPONSE
    # =============================

    updated_plan = dict(plan)
    if adjustments:
        updated_plan["adjustments"] = adjustments

    return {
        "status": status,
        "feedback": feedback,
        "trend": trend,
        "performance": performance,
        "updated_plan": updated_plan,
        "adjustments": adjustments,
        "escalation": escalation,
        "alert_level": alert_level,
        "llm_insight": llm_insight or "No AI insight available"
    }
