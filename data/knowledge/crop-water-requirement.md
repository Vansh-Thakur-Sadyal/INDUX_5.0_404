---
id: crop-water-requirement
title: Crop water requirement and evapotranspiration
topics: irrigation,water,weather
crops: wheat,rice,maize,cotton
source: Saarthi knowledge base - general agronomy notes (verify locally)
---

Crop water requirement is estimated as reference evapotranspiration (ET0) multiplied
by a crop coefficient (Kc) for the current growth stage.

ET0 is a weather-driven value: it rises with temperature, radiation and wind, and falls
with humidity. Open-Meteo and similar services publish ET0 for a location.

Kc follows the crop cycle:
- Initial stage (emergence to about 10 percent ground cover): Kc around 0.3 to 0.5.
- Development stage: Kc rises towards the mid-season value.
- Mid season (full canopy to start of maturity): Kc around 1.05 to 1.20 for most
  cereals, around 1.15 for well-watered rice.
- Late season (maturity and senescence): Kc falls to about 0.3 to 0.6.

Worked method:
1. Take ET0 for the period, in mm per day.
2. Multiply by the stage Kc to get crop evapotranspiration (ETc) in mm per day.
3. Subtract effective rainfall for the period.
4. Divide by the application efficiency of the irrigation method.
5. Convert the depth to a volume: 1 mm over 1 hectare equals 10,000 litres.

The result is an estimate. It assumes the stage is known, the crop is healthy and the
soil is not restricting root growth. State those assumptions whenever the number is
given to a farmer.
