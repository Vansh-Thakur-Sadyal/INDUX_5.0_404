def fertilizer_tool(n, p, k, soil_ph):
    if soil_ph < 6:
        return "Soil acidic → Add lime and balanced fertilizer."
    elif soil_ph > 7.5:
        return "Soil alkaline → Use sulfur and organic compost."

    if n < 50:
        return "Low Nitrogen → Apply nitrogen-rich fertilizer."
    elif p < 30:
        return "Low Phosphorus → Add phosphate fertilizer."
    elif k < 30:
        return "Low Potassium → Add potash fertilizer."

    return "NPK levels adequate → Maintain balanced fertilization."
