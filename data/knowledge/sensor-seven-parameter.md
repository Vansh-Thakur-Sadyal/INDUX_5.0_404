---
id: sensor-seven-parameter
title: Interpreting the seven-parameter soil sensor
topics: sensor,soil,data-quality,soil-health
crops: 
source: Saarthi knowledge base - general agronomy notes (verify locally)
---

The seven-in-one RS485 Modbus soil sensor used in this project reports nitrogen,
phosphorus, potassium, soil temperature, soil moisture, electrical conductivity and pH.

What the readings are, and are not:
- Nitrogen, phosphorus and potassium from this class of sensor are derived indirectly,
  largely from the soil's electrical properties. They are a screening signal for
  relative change over time in one field, not a laboratory nutrient measurement.
  A fertiliser dose must not be calculated from them alone.
- Soil temperature and moisture are direct physical measurements and are the most
  dependable outputs, provided the probe has good soil contact at a known depth.
- EC and pH are usable for screening once the probe has been calibrated against a known
  standard, and they drift over time.

Data quality rules applied before any reading is used:
- The packet must contain all seven fields with the expected register mapping, baud
  rate and slave id from the sensor manual. A provisional mapping is not a verified one.
- Every reading carries a timestamp, a device id, a parcel id and a mode flag of either
  real or simulated.
- Readings outside physically plausible ranges are marked invalid rather than silently
  clamped. Plausible field ranges used for screening: moisture 0 to 100 percent,
  temperature -10 to 60 C, pH 3 to 10, EC 0 to 10 dS/m, N P K 0 to 2000 mg/kg.
- A reading older than the freshness limit is marked stale and is not presented as the
  current state of the field.
- A simulated reading is never presented as a measurement. It is labelled at every
  layer from the generator to the dashboard.

A single probe samples one point at one depth. Root zone conditions vary across a field,
so treat the value as representative only of that spot.
