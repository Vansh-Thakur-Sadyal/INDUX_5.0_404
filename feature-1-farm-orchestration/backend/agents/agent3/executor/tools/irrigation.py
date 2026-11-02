def irrigation_tool(soil_moisture, temperature):
    if soil_moisture < 30:
        return "Soil is dry → Increase irrigation immediately."
    elif temperature > 35:
        return "High temperature → Increase irrigation frequency."
    else:
        return "Soil moisture adequate → Maintain normal irrigation."
