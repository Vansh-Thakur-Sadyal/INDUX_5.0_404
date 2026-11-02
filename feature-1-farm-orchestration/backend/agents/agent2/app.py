# ==========================================
# AGENT 2 (INTELLIGENT + UI CONTROLLED)
# ==========================================

import os
import json
import random
import numpy as np
import cv2
from sklearn.ensemble import IsolationForest

# Absolute path so the dataset loads no matter which folder the
# server is started from. Points at the labelled train set
# (diseased/fresh cotton leaf/plant classes).
DATASET_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Dataset", "Cotton Disease", "train"
)
# LLM model is configured once in backend/llm_client.py (MISTRAL_MODEL)

# =========================
# RULE BASE
# =========================

CROP_RULES = {
    "rice": {"temp": (20, 35), "moisture": 60, "humidity": 70},
    "wheat": {"temp": (10, 25), "moisture": 40, "humidity": 50},
    "cotton": {"temp": (21, 30), "moisture": 35, "humidity": 60},
    "sugarcane": {"temp": (20, 38), "moisture": 65, "humidity": 65}
}

# =========================
# TOOL 1: RULE ENGINE
# =========================

def tool_rule_engine(data):
    """
    Risks are (name, level, severity, confidence) tuples.
    Severity and confidence are derived from HOW FAR a reading is
    outside the crop's safe range — not hardcoded constants.
    """
    crop = data.get("crop", "rice")
    rules = CROP_RULES.get(crop, CROP_RULES["rice"])
    risks = []

    temp = data.get("temperature", 25)
    lo, hi = rules["temp"]
    if not (lo <= temp <= hi):
        dist = (lo - temp) if temp < lo else (temp - hi)
        sev = round(min(1.0, 0.5 + dist / 20), 2)
        conf = round(min(0.95, 0.6 + dist / 40), 2)
        risks.append(("Temperature Stress", "HIGH", sev, conf))

    moisture = data.get("soil_moisture", 50)
    if moisture < rules["moisture"]:
        deficit = rules["moisture"] - moisture
        sev = round(min(1.0, 0.4 + deficit / rules["moisture"]), 2)
        conf = round(min(0.95, 0.6 + deficit / 100), 2)
        risks.append(("Water Stress", "HIGH", sev, conf))

    humidity = data.get("humidity", 50)
    if humidity < rules["humidity"]:
        gap = rules["humidity"] - humidity
        sev = round(min(1.0, 0.3 + gap / 60), 2)
        conf = round(min(0.9, 0.5 + gap / 100), 2)
        risks.append(("Pest Probability", "MEDIUM", sev, conf))

    ndvi = data.get("ndvi", 0.5)
    if ndvi < 0.4:
        short = 0.4 - ndvi
        sev = round(min(1.0, 0.5 + short * 2), 2)
        conf = round(min(0.9, 0.6 + short), 2)
        risks.append(("Nutrient Deficiency", "HIGH", sev, conf))

    return risks

# =========================
# TOOL 2: ML ANOMALY
# =========================

def train_sensor_model():
    data = np.random.rand(500, 4)
    model = IsolationForest(contamination=0.1)
    model.fit(data)
    return model

sensor_model = train_sensor_model()

def tool_ml_anomaly(data):
    X = np.array([[data.get("temperature", 25),
                   data.get("humidity", 50),
                   data.get("soil_moisture", 50),
                   data.get("ndvi", 0.5)]])

    if sensor_model.predict(X)[0] == -1:
        # Anomaly strength from the model's own score: more negative
        # decision_function = more anomalous.
        score = abs(float(sensor_model.decision_function(X)[0]))
        sev = round(min(1.0, 0.5 + score * 2), 2)
        conf = round(min(0.9, 0.55 + score * 2), 2)
        return [("Environmental Anomaly", "HIGH", sev, conf)]
    return []

# =========================
# TOOL 3: IMAGE MODEL
# =========================

image_model = None
class_names = ["healthy"]

def load_image_model():
    global image_model, class_names

    if image_model is not None:
        return

    try:
        import tensorflow as tf

        dataset = tf.keras.preprocessing.image_dataset_from_directory(
            DATASET_PATH,
            image_size=(128, 128),
            batch_size=32
        )

        class_names = dataset.class_names

        model = tf.keras.Sequential([
            tf.keras.layers.Rescaling(1./255),
            tf.keras.layers.Conv2D(32, 3, activation='relu'),
            tf.keras.layers.MaxPooling2D(),
            tf.keras.layers.Conv2D(64, 3, activation='relu'),
            tf.keras.layers.MaxPooling2D(),
            tf.keras.layers.Flatten(),
            tf.keras.layers.Dense(64, activation='relu'),
            tf.keras.layers.Dense(len(class_names), activation='softmax')
        ])

        model.compile(optimizer='adam',
                      loss='sparse_categorical_crossentropy',
                      metrics=['accuracy'])

        model.fit(dataset, epochs=1, verbose=0)

        image_model = model

    except Exception:
        image_model = None

def tool_image_analysis(image_path, crop):
    if image_model is None:
        return [], "no_model"

    img = cv2.imread(image_path)
    img = cv2.resize(img, (128,128))
    img = np.expand_dims(img, axis=0)

    pred = image_model.predict(img, verbose=0)
    label = class_names[np.argmax(pred)]
    prob = float(np.max(pred))   # the CNN's actual confidence in its prediction

    risks = []
    # Dataset class names use "fresh" for healthy plants
    if not any(k in label.lower() for k in ("healthy", "fresh")):
        sev = round(min(1.0, 0.5 + prob / 2), 2)
        risks.append(("Disease Detected", "HIGH", sev, round(prob, 2)))

    return risks, label

# =========================
# 🧠 HYBRID DECISION ENGINE
# =========================

def decide_analysis_mode(input_data):

    # 🟢 Manual mode (from UI)
    try:
        if isinstance(input_data, dict):
            agent2_input = input_data.get("agent2", {})
            if agent2_input.get("analysis"):
                mode = agent2_input.get("analysis")
                print("🎛️ Manual Mode:", mode)
                return mode
    except:
        pass

    # 🔵 LLM mode (Mistral API)
    try:
        import sys
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
        from llm_client import mistral_chat

        raw = mistral_chat([{
            "role": "user",
            "content": f"""
            User input: {input_data}

            Choose analysis type:
            options = [rule, ml, image, full]

            Return only one word.
            """
        }]).lower()

        for key in ["rule", "ml", "image", "full"]:
            if key in raw:
                print("🧠 LLM Decision:", key)
                return key

        return "full"

    except Exception:
        return "full"

# =========================
# UTILITIES
# =========================

def get_random_image():
    imgs = []
    for root, _, files in os.walk(DATASET_PATH):
        for f in files:
            if f.endswith((".jpg", ".png", ".jpeg")):
                imgs.append(os.path.join(root, f))
    return random.choice(imgs) if imgs else None

# =========================
# PIPELINE ENTRY  ← UPDATED (Step 3 Fix)
# =========================

def main(agent1_output, ui_input=None):

    # UI values from the dashboard take priority over collected/default data
    ui = ui_input if isinstance(ui_input, dict) else {}
    ui_sensor = ui.get("agent1") if isinstance(ui.get("agent1"), dict) else {}
    ui_agent2 = ui.get("agent2") if isinstance(ui.get("agent2"), dict) else {}

    # Safely extract agent2-relevant input, ensures no crash
    try:
        agent2_input = ui if ui else (agent1_output if isinstance(agent1_output, dict) else {})
    except:
        agent2_input = {}

    if isinstance(agent1_output, list) and len(agent1_output) > 0:
        record = agent1_output[0]
    else:
        record = {}

    data = {
        "crop": str(ui_agent2.get("mode", "cotton")).lower(),
        "temperature": ui_sensor.get("temperature")
            if ui_sensor.get("temperature") is not None
            else (record.get("weather", {}).get("temperature") or 25),
        "humidity": ui_sensor.get("humidity")
            if ui_sensor.get("humidity") is not None
            else (record.get("weather", {}).get("humidity") or 50),
        "soil_moisture": ui_sensor.get("soil_moisture")
            if ui_sensor.get("soil_moisture") is not None
            else (record.get("sensor", {}).get("soil_moisture") or 50),
        "ndvi": 0.5
    }

    mode = decide_analysis_mode(agent2_input)

    risks = []
    disease = "unknown"

    image_path = get_random_image()

    if mode == "rule":
        risks += tool_rule_engine(data)

    elif mode == "ml":
        risks += tool_ml_anomaly(data)

    elif mode == "image":
        load_image_model()
        r, d = tool_image_analysis(image_path, data["crop"])
        risks += r
        disease = d

    else:
        risks += tool_rule_engine(data)
        risks += tool_ml_anomaly(data)

        load_image_model()
        r, d = tool_image_analysis(image_path, data["crop"])
        risks += r
        disease = d

    # Sensitivity slider from the dashboard: low sensitivity only keeps
    # severe risks, high sensitivity reports everything.
    try:
        sensitivity = float(ui_agent2.get("sensitivity", 100))
    except (TypeError, ValueError):
        sensitivity = 100

    if sensitivity < 40:
        risks = [r for r in risks if r[1] == "HIGH"]
    elif sensitivity < 75:
        risks = [r for r in risks if r[1] in ("HIGH", "MEDIUM")]

    score_map = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    score = sum(score_map[r[1]] for r in risks)

    data["analysis_mode"] = mode
    data["sensitivity"] = sensitivity

    return {
        "agent2_output": {
            "risks": risks,
            "disease": disease,
            "risk_score": score,
            "status": "SAFE" if score == 0 else "ALERT"
        },
        "input_used": data
    }

# =========================
# TEST MODE
# =========================

if __name__ == "__main__":
    print("🚀 Agent2 Running...")

    print(json.dumps(main({
        "agent2": {"analysis": "rule"}
    }), indent=4))
