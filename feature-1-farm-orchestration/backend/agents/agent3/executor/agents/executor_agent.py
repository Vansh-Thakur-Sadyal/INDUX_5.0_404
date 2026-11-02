from executor.tools.irrigation import irrigation_tool
from executor.tools.fertilizer import fertilizer_tool
from executor.tools.pest import pest_control_tool
from executor.tools.environment import temperature_tool


def executor_agent(actions, data):

    results = {}

    # 🟢 IRRIGATION (FIXED: supports both names)
    if "irrigate" in actions or "irrigation" in actions:
        if "soil_moisture" in data and "air_temp" in data:
            results["irrigation"] = irrigation_tool(
                data["soil_moisture"],
                data["air_temp"]
            )
        else:
            results["irrigation"] = "Missing data for irrigation"

    # 🟢 FERTILIZER
    if "fertilizer" in actions:
        required_keys = ["nitrogen", "phosphorus", "potassium", "soil_ph"]
        if all(k in data for k in required_keys):
            results["fertilizer"] = fertilizer_tool(
                data["nitrogen"],
                data["phosphorus"],
                data["potassium"],
                data["soil_ph"]
            )
        else:
            results["fertilizer"] = "Missing data for fertilizer"

    # 🟢 PEST CONTROL
    if "pest_control" in actions:
        if "humidity" in data:
            results["pest_control"] = pest_control_tool(data["humidity"])
        else:
            results["pest_control"] = "Missing data for pest control"

    # 🟢 TEMPERATURE
    if "temperature" in actions:
        if "air_temp" in data:
            results["temperature"] = temperature_tool(data["air_temp"])
        else:
            results["temperature"] = "Missing data for temperature"

    return results
