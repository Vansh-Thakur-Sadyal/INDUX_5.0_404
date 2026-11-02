# ==========================================
# AGENT ORCHESTRATOR (FINAL UPGRADED)
# ==========================================

import sys
import os
import time
from datetime import datetime

sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import progress

# Import agents
from agent1.main import main as agent1_main
from agent2.app import main as agent2_main
from agent3.orchestrator.main import main as agent3_main


class AgentOrchestrator:

    def __init__(self):
        print("✅ Agentic AI Orchestrator Initialized")

    def run_pipeline(self, user_input):

        pipeline_start = time.time()
        progress.start()

        pipeline_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "steps": []
        }

        try:
            print("\n📥 FULL INPUT:", user_input)

            # ==========================================
            # STEP 1 → AGENT 1
            # ==========================================
            print("\n🔹 STEP 1 → Agent1 (Data Engine)")
            progress.set_step(1, "Agent 1: Collecting sensor data")

            start = time.time()

            try:
                output1 = agent1_main(user_input)
                print("🧾 Agent1 Output:", str(output1)[:300])
            except Exception as e:
                raise Exception(f"Agent1 failed → {e}")

            duration = time.time() - start

            pipeline_log["steps"].append({
                "agent": "Agent1",
                "status": "success",
                "time": round(duration, 3)
            })

            # ==========================================
            # STEP 2 → AGENT 2
            # ==========================================
            print("\n🔹 STEP 2 → Agent2 (Analysis Engine)")
            progress.set_step(2, "Agent 2: Running risk analysis")

            start = time.time()

            try:
                output2 = agent2_main(output1, user_input)
                print("🧾 Agent2 Output:", str(output2)[:300])
            except Exception as e:
                raise Exception(f"Agent2 failed → {e}")

            duration = time.time() - start

            pipeline_log["steps"].append({
                "agent": "Agent2",
                "status": "success",
                "time": round(duration, 3)
            })

            # ==========================================
            # STEP 3 → AGENT 3
            # ==========================================
            print("\n🔹 STEP 3 → Agent3 (Decision Engine)")
            progress.set_step(3, "Agent 3: Generating decision plan")

            start = time.time()

            try:
                output3 = agent3_main(output2, user_input)
                print("🧾 Agent3 Output:", str(output3)[:300])
            except Exception as e:
                raise Exception(f"Agent3 failed → {e}")

            duration = time.time() - start

            pipeline_log["steps"].append({
                "agent": "Agent3",
                "status": "success",
                "time": round(duration, 3)
            })

            # ==========================================
            # FINAL OUTPUT
            # ==========================================
            total_time = time.time() - pipeline_start

            print("\n✅ PIPELINE COMPLETE")
            progress.finish(success=True)

            return {
                "status": "success",
                "total_time": round(total_time, 3),

                # 🔥 For dashboard
                "pipeline_log": pipeline_log,

                # 🔥 Each agent output
                "agent_outputs": {
                    "agent1": output1,
                    "agent2": output2,
                    "agent3": output3
                },

                # 🔥 Final result
                "final_output": output3
            }

        except Exception as e:

            print("\n❌ PIPELINE FAILED:", str(e))
            progress.finish(success=False)

            pipeline_log["steps"].append({
                "agent": "FAILED",
                "status": "error",
                "error": str(e)
            })

            return {
                "status": "error",
                "error": str(e),
                "pipeline_log": pipeline_log
            }


# ==========================================
# TEST MODE
# ==========================================

if __name__ == "__main__":
    orchestrator = AgentOrchestrator()

    result = orchestrator.run_pipeline({
        "query": "crop health analysis",
        "agent1": {},
        "agent2": {},
        "agent3": {}
    })

    print("\n✅ FINAL OUTPUT:\n", result)
