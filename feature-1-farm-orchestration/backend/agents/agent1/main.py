# ==========================================
# AGENT 1 (INTELLIGENT + UI CONTROLLED)
# ==========================================

from agent1.collectors.sensor_collector import SensorCollector
from agent1.collectors.weather_collector import WeatherCollector
from agent1.collectors.image_collector import ImageCollector
from agent1.collectors.market_collector import MarketCollector

from agent1.processors.standardization import DataStandardizer
from agent1.processors.synchronization import DataSynchronizer
from agent1.processors.reliability import ReliabilityScorer

from agent1.storage.json_storage import JSONStorage
from agent1.storage.buffer import Buffer

from agent1.utils.logger import get_logger
from agent1.utils.mistral_integration import MistralValidator

from agent1.api.routes import DataAPI

import uvicorn
from fastapi import FastAPI
from datetime import datetime
import os

# ==========================================
# INITIALIZATION
# ==========================================

logger = get_logger(__name__)

sensor_collector = SensorCollector()
weather_collector = WeatherCollector()
image_collector = ImageCollector()
market_collector = MarketCollector()

standardizer = DataStandardizer()
synchronizer = DataSynchronizer()
scorer = ReliabilityScorer()

BASE_DIR = os.path.dirname(__file__)
DATA_FILE_PATH = os.path.join(BASE_DIR, "data", "processed_data.json")

storage = JSONStorage(file_path=DATA_FILE_PATH)
buffer = Buffer(max_size=10)

validator = MistralValidator()

# ==========================================
# 🧠 HYBRID DECISION ENGINE (UI + LLM)
# ==========================================

def interpret_input(input_data):

    # 🟢 If UI provides manual mode → use it directly
    try:
        if isinstance(input_data, dict):
            agent1_input = input_data.get("agent1", {})

            if agent1_input.get("mode") == "manual":
                source = agent1_input.get("source", "all")
                logger.info(f"🎛️ Manual Mode → {source}")
                return source
    except:
        pass

    # 🔵 Otherwise use LLM (Mistral API)
    try:
        import sys, os
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
        from llm_client import mistral_chat

        decision = mistral_chat([{
            "role": "user",
            "content": f"""
            User input: {input_data}

            Decide which data sources to use:
            options = [weather, soil, market, image, all]

            Return only one word.
            """
        }]).lower()

        # Clean output (important)
        for key in ["weather", "soil", "market", "image", "all"]:
            if key in decision:
                logger.info(f"🧠 LLM Decision → {key}")
                return key

        return "all"

    except Exception as e:
        logger.warning(f"LLM failed → fallback ALL: {e}")
        return "all"

# ==========================================
# CORE PIPELINE FUNCTION
# ==========================================

def run_pipeline(input_data=None):

    try:
        logger.info("🔹 Agent1: Starting intelligent data collection...")

        decision = interpret_input(input_data)

        sensor_data = []
        weather_data = []
        image_data = []
        market_data = []

        # ==================================
        # SMART COLLECTION
        # ==================================

        if decision == "all":
            sensor_data = sensor_collector.collect()
            weather_data = weather_collector.collect()
            image_data = image_collector.collect()
            market_data = market_collector.collect()

        elif decision == "weather":
            weather_data = weather_collector.collect()

        elif decision == "soil":
            sensor_data = sensor_collector.collect()

        elif decision == "market":
            market_data = market_collector.collect()

        elif decision == "image":
            image_data = image_collector.collect()

        else:
            # fallback safe default
            sensor_data = sensor_collector.collect()
            weather_data = weather_collector.collect()

        # ==================================
        # PROCESSING
        # ==================================

        timestamp = datetime.utcnow().isoformat()

        standardized_data = standardizer.standardize_data(
            timestamp=timestamp,
            sensor_data=sensor_data,
            weather_data=weather_data,
            image_data=image_data,
            market_data=market_data
        )

        synced_data = synchronizer.synchronize(standardized_data)

        processed_records = []

        for record in synced_data:
            try:
                record["reliability_score"] = scorer.compute(record)
                record["validation"] = validator.validate(record)

            except Exception as e:
                logger.warning(f"Processing failed: {e}")
                record["reliability_score"] = 0
                record["validation"] = {
                    "status": "failed",
                    "error": str(e)
                }

            processed_records.append(record)

        logger.info(f"✅ Agent1 processed {len(processed_records)} records")

        # ==================================
        # STORAGE
        # ==================================

        try:
            storage.append(processed_records)
        except Exception:
            buffer.add(processed_records)

        if buffer.get_all():
            buffer.flush(storage.append)

        return processed_records

    except Exception as e:
        logger.error(f"❌ Agent1 pipeline error: {e}")
        return {"error": str(e)}

# ==========================================
# PIPELINE ENTRY  ← UPDATED (Step 2 Fix)
# ==========================================

def _ui_fallback_record(agent1_input):
    """
    Collectors can return nothing (e.g. the LLM picks a source with no
    data). The dashboard inputs are still real measurements, so build
    one record from them — reliability is then always computable.
    """
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "field_id": "UI_INPUT",
        "crop": agent1_input.get("crop", "unknown"),
        "sensor": {"soil_moisture": agent1_input.get("soil_moisture")},
        "weather": {
            "temperature": agent1_input.get("temperature"),
            "humidity": agent1_input.get("humidity"),
        },
        "meta": {
            "data_sources": {
                "sensor": True, "weather": True, "image": False, "market": False
            }
        },
    }
    record["reliability_score"] = scorer.compute(record)
    record["validation"] = {
        "is_valid": True,
        "issues": ["Record built directly from dashboard inputs"],
        "suggestions": [],
    }
    return [record]


def main(input_data):
    agent1_input = {}
    try:
        # Extract only the agent1-relevant part of the input
        agent1_input = input_data.get("agent1", {}) or {}
        result = run_pipeline(agent1_input)
    except Exception:
        result = run_pipeline()

    if not isinstance(result, list) or len(result) == 0:
        result = _ui_fallback_record(agent1_input)

    return result

# ==========================================
# STANDALONE MODE
# ==========================================

if __name__ == "__main__":

    print("🚀 Running Agent1...\n")

    result = main({
        "agent1": {"mode": "manual", "source": "weather"}
    })

    print("\nSample Output:",
          result[:1] if isinstance(result, list) else result)

    app = FastAPI()
    api = DataAPI(data_file_path=DATA_FILE_PATH)
    app.include_router(api.router)

    uvicorn.run(app, host="0.0.0.0", port=8000)
