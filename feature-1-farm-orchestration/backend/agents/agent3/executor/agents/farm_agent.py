import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from llm_client import mistral_chat

from tools.irrigation import irrigation_tool
from tools.fertilizer import fertilizer_tool
from tools.pest import pest_control_tool
from tools.environment import temperature_tool


def run_farm_agent(data):

    # Extract inputs
    crop = data["crop"]
    soil_moisture = data["soil_moisture"]
    soil_temp = data["soil_temp"]
    air_temp = data["air_temp"]
    humidity = data["humidity"]
    soil_ph = data["soil_ph"]
    n = data["nitrogen"]
    p = data["phosphorus"]
    k = data["potassium"]

    # Tool outputs
    irrigation = irrigation_tool(soil_moisture, air_temp)
    fertilizer = fertilizer_tool(n, p, k, soil_ph)
    pest = pest_control_tool(humidity)
    temp_advice = temperature_tool(air_temp)

    # LLM reasoning (Mistral API)
    prompt = f"""
    You are an advanced agricultural AI decision agent.

    Crop: {crop}

    Sensor Data:
    - Soil Moisture: {soil_moisture}%
    - Soil Temperature: {soil_temp}°C
    - Air Temperature: {air_temp}°C
    - Humidity: {humidity}%
    - Soil pH: {soil_ph}
    - Nitrogen: {n}
    - Phosphorus: {p}
    - Potassium: {k}

    Tool Analysis:
    - Irrigation: {irrigation}
    - Fertilization: {fertilizer}
    - Pest Control: {pest}
    - Temperature Advice: {temp_advice}

    Generate a structured farm action plan with:
    1. Irrigation Plan
    2. Fertilization Plan
    3. Pest Control Plan
    4. Environmental Adjustments
    5. Final Smart Recommendations
    """

    return mistral_chat([{"role": "user", "content": prompt}])
