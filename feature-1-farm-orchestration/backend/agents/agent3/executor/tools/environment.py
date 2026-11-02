def temperature_tool(air_temp):
    if air_temp > 35:
        return "Heat stress risk → Consider shading or mulching."
    elif air_temp < 10:
        return "Cold stress → Protect crops using covers."
    return "Temperature optimal for crop growth."
