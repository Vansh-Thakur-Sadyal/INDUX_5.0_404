"""
Constraint Optimization Agent - SH-AGR-001
=========================================
Optimizes field action plans (what/when/where) given:
  - Risk signals from sensor/weather/imagery agents
  - Market data
  - Hard constraints (budget, weather windows, safety, crop stage)
  - Soft constraints (cost efficiency, timeliness)

Designed to be called as a module by an orchestration layer.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from enum import Enum

import numpy as np
from scipy.optimize import linprog, minimize
from pulp import (
    LpProblem, LpVariable, LpMinimize, LpMaximize,
    lpSum, LpStatus, value, LpBinary, LpContinuous
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ConstraintOptAgent")


# ─────────────────────────────────────────────
# Data Models (Input Schema)
# ─────────────────────────────────────────────

class ActionType(str, Enum):
    IRRIGATE = "irrigate"
    FERTILIZE = "fertilize"
    SPRAY_PESTICIDE = "spray_pesticide"
    HARVEST = "harvest"
    SOIL_AMENDMENT = "soil_amendment"
    SCOUTING = "scouting"
    NO_ACTION = "no_action"


@dataclass
class RiskSignal:
    """Input from sensor/weather/imagery agents."""
    risk_type: str          # "water_stress", "pest", "nutrient_gap", "disease"
    severity: float         # 0.0 (low) to 1.0 (critical)
    affected_field_id: str
    confidence: float       # 0.0 to 1.0
    detected_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict = field(default_factory=dict)


@dataclass
class WeatherWindow:
    """Safe weather window for field operations."""
    field_id: str
    start_time: datetime
    end_time: datetime
    wind_speed_kmh: float
    rain_probability: float  # 0.0 to 1.0
    temperature_c: float
    is_spray_safe: bool
    is_irrigation_needed: bool


@dataclass
class FieldConstraints:
    """Hard constraints per field."""
    field_id: str
    total_budget_inr: float
    max_water_liters: float
    crop_stage: str          # "seedling", "vegetative", "flowering", "fruiting", "harvest"
    area_hectares: float
    forbidden_actions: List[ActionType] = field(default_factory=list)
    priority: int = 1        # 1=high, 2=medium, 3=low


@dataclass
class ActionOption:
    """A candidate action with cost and effectiveness."""
    action_type: ActionType
    field_id: str
    cost_inr: float
    water_liters: float
    effectiveness_score: float   # 0.0 to 1.0 (how well it addresses risk)
    time_window_start: datetime
    time_window_end: datetime
    duration_hours: float
    notes: str = ""


@dataclass
class OptimizedPlan:
    """Output: recommended field actions."""
    field_id: str
    selected_actions: List[ActionOption]
    total_cost_inr: float
    total_water_liters: float
    expected_risk_reduction: float  # 0.0 to 1.0
    optimization_status: str
    warnings: List[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self):
        return {
            "field_id": self.field_id,
            "actions": [
                {
                    "type": a.action_type.value,
                    "cost_inr": a.cost_inr,
                    "water_liters": a.water_liters,
                    "start": a.time_window_start.isoformat(),
                    "end": a.time_window_end.isoformat(),
                    "notes": a.notes,
                }
                for a in self.selected_actions
            ],
            "total_cost_inr": self.total_cost_inr,
            "total_water_liters": self.total_water_liters,
            "expected_risk_reduction": round(self.expected_risk_reduction, 3),
            "status": self.optimization_status,
            "warnings": self.warnings,
            "generated_at": self.generated_at.isoformat(),
        }


# ─────────────────────────────────────────────
# Action Generator
# ─────────────────────────────────────────────

class ActionGenerator:
    """
    Generates candidate actions based on detected risks.
    In production: integrate with agronomist knowledge base / LLM agent.
    """

    # Cost lookup (INR/hectare) and water (liters/hectare) — editable
    ACTION_COSTS = {
        ActionType.IRRIGATE:         (800,   5000),
        ActionType.FERTILIZE:        (1200,  0),
        ActionType.SPRAY_PESTICIDE:  (1500,  200),
        ActionType.HARVEST:          (3000,  0),
        ActionType.SOIL_AMENDMENT:   (2000,  0),
        ActionType.SCOUTING:         (300,   0),
        ActionType.NO_ACTION:        (0,     0),
    }

    RISK_ACTION_MAP = {
        "water_stress":  [ActionType.IRRIGATE, ActionType.SCOUTING],
        "heat_stress":   [ActionType.IRRIGATE, ActionType.SCOUTING],
        "pest":          [ActionType.SPRAY_PESTICIDE, ActionType.SCOUTING],
        "nutrient_gap":  [ActionType.FERTILIZE, ActionType.SOIL_AMENDMENT],
        "disease":       [ActionType.SPRAY_PESTICIDE, ActionType.SCOUTING],
        "anomaly":       [ActionType.SOIL_AMENDMENT, ActionType.SCOUTING],
    }

    def generate(
        self,
        risks: List[RiskSignal],
        weather_windows: List[WeatherWindow],
        constraints: FieldConstraints,
    ) -> List[ActionOption]:
        candidates = []
        now = datetime.utcnow()

        for risk in risks:
            if risk.affected_field_id != constraints.field_id:
                continue

            actions_for_risk = self.RISK_ACTION_MAP.get(risk.risk_type, [ActionType.SCOUTING])

            for action_type in actions_for_risk:
                if action_type in constraints.forbidden_actions:
                    continue

                cost_per_ha, water_per_ha = self.ACTION_COSTS[action_type]
                total_cost = cost_per_ha * constraints.area_hectares
                total_water = water_per_ha * constraints.area_hectares

                # Pick the first safe weather window
                window = self._pick_window(action_type, weather_windows, constraints.field_id)
                if window is None:
                    window_start = now + timedelta(hours=2)
                    window_end = now + timedelta(hours=6)
                else:
                    window_start = window.start_time
                    window_end = window.end_time

                # Stagger candidates into sequential 2-hour slots — actions on
                # one field happen one after another, so the ILP's non-overlap
                # rule doesn't force a single-action plan.
                slot = timedelta(hours=2 * len(candidates))
                window_start = window_start + slot
                window_end = window_start + timedelta(hours=2)

                # Effectiveness: how well this action addresses the risk
                effectiveness = self._effectiveness(action_type, risk)

                candidates.append(ActionOption(
                    action_type=action_type,
                    field_id=constraints.field_id,
                    cost_inr=total_cost,
                    water_liters=total_water,
                    effectiveness_score=effectiveness * risk.severity,
                    time_window_start=window_start,
                    time_window_end=window_end,
                    duration_hours=2.0,
                    notes=f"Risk: {risk.risk_type} | Severity: {risk.severity:.2f} | Confidence: {risk.confidence:.2f}",
                ))

        # One candidate per action type — keep the strongest, so the plan
        # doesn't repeat "irrigate" for every risk it addresses.
        best = {}
        for c in candidates:
            k = c.action_type
            if k not in best or c.effectiveness_score > best[k].effectiveness_score:
                best[k] = c
        candidates = list(best.values())

        # Always include no-action as fallback
        candidates.append(ActionOption(
            action_type=ActionType.NO_ACTION,
            field_id=constraints.field_id,
            cost_inr=0, water_liters=0,
            effectiveness_score=0.0,
            time_window_start=now,
            time_window_end=now + timedelta(hours=24),
            duration_hours=0,
        ))

        return candidates

    def _pick_window(self, action: ActionType, windows: List[WeatherWindow], field_id: str):
        for w in windows:
            if w.field_id != field_id:
                continue
            if action == ActionType.SPRAY_PESTICIDE and not w.is_spray_safe:
                continue
            return w
        return None

    def _effectiveness(self, action: ActionType, risk: RiskSignal) -> float:
        mapping = {
            ("water_stress", ActionType.IRRIGATE):        0.95,
            ("water_stress", ActionType.SCOUTING):        0.20,
            ("heat_stress",  ActionType.IRRIGATE):        0.75,
            ("heat_stress",  ActionType.SCOUTING):        0.25,
            ("pest",         ActionType.SPRAY_PESTICIDE): 0.90,
            ("pest",         ActionType.SCOUTING):        0.30,
            ("nutrient_gap", ActionType.FERTILIZE):       0.88,
            ("nutrient_gap", ActionType.SOIL_AMENDMENT):  0.70,
            ("disease",      ActionType.SPRAY_PESTICIDE): 0.85,
            ("disease",      ActionType.SCOUTING):        0.25,
            ("anomaly",      ActionType.SOIL_AMENDMENT):  0.55,
            ("anomaly",      ActionType.SCOUTING):        0.40,
        }
        return mapping.get((risk.risk_type, action), 0.40)


# ─────────────────────────────────────────────
# Constraint Optimization Engine (ILP)
# ─────────────────────────────────────────────

class ConstraintOptimizationEngine:
    """
    Integer Linear Program:
      Maximize: sum(effectiveness_i * x_i)
      Subject to:
        sum(cost_i * x_i)  <= budget
        sum(water_i * x_i) <= max_water
        x_i in {0, 1}      (binary: do or don't)
        No overlapping windows for same field
    """

    def optimize(
        self,
        candidates: List[ActionOption],
        constraints: FieldConstraints,
    ) -> OptimizedPlan:

        if not candidates:
            return OptimizedPlan(
                field_id=constraints.field_id,
                selected_actions=[],
                total_cost_inr=0,
                total_water_liters=0,
                expected_risk_reduction=0,
                optimization_status="NO_CANDIDATES",
                warnings=["No candidate actions generated."],
            )

        n = len(candidates)
        warnings = []

        # ── ILP with PuLP ──────────────────────────────────────
        prob = LpProblem(f"FarmAction_{constraints.field_id}", LpMaximize)
        x = [LpVariable(f"x_{i}", cat=LpBinary) for i in range(n)]

        # Objective: maximize total risk reduction (effectiveness)
        prob += lpSum(candidates[i].effectiveness_score * x[i] for i in range(n))

        # Budget constraint
        prob += lpSum(candidates[i].cost_inr * x[i] for i in range(n)) <= constraints.total_budget_inr

        # Water constraint
        prob += lpSum(candidates[i].water_liters * x[i] for i in range(n)) <= constraints.max_water_liters

        # No-action: only select if nothing else selected
        no_action_idx = [i for i, c in enumerate(candidates) if c.action_type == ActionType.NO_ACTION]
        real_action_idx = [i for i in range(n) if i not in no_action_idx]
        if no_action_idx and real_action_idx:
            for na_i in no_action_idx:
                prob += x[na_i] <= 1 - lpSum(x[j] for j in real_action_idx) / max(len(real_action_idx), 1)

        # Time-window non-overlap: two actions on same field can't overlap
        for i in range(n):
            for j in range(i + 1, n):
                ci, cj = candidates[i], candidates[j]
                overlaps = (
                    ci.time_window_start < cj.time_window_end and
                    cj.time_window_start < ci.time_window_end
                )
                if overlaps and ci.field_id == cj.field_id:
                    prob += x[i] + x[j] <= 1

        prob.solve()
        status = LpStatus[prob.status]
        logger.info(f"[{constraints.field_id}] ILP status: {status}")

        selected = [candidates[i] for i in range(n) if value(x[i]) and value(x[i]) > 0.5]

        if not selected:
            warnings.append("No feasible action within constraints. Consider relaxing budget or water limits.")
            selected = []

        total_cost = sum(a.cost_inr for a in selected)
        total_water = sum(a.water_liters for a in selected)
        risk_reduction = min(sum(a.effectiveness_score for a in selected), 1.0)

        # Budget utilization warning
        if total_cost > constraints.total_budget_inr * 0.95:
            warnings.append("Budget nearly exhausted. Monitor for additional risks.")

        return OptimizedPlan(
            field_id=constraints.field_id,
            selected_actions=selected,
            total_cost_inr=total_cost,
            total_water_liters=total_water,
            expected_risk_reduction=risk_reduction,
            optimization_status=status,
            warnings=warnings,
        )


# ─────────────────────────────────────────────
# Main Agent Interface
# ─────────────────────────────────────────────

class ConstraintOptimizationAgent:
    """
    Public interface for the orchestration layer.

    Usage:
        agent = ConstraintOptimizationAgent()
        plan = agent.run(risks, weather_windows, constraints)
        print(plan.to_dict())
    """

    def __init__(self):
        self.action_generator = ActionGenerator()
        self.optimizer = ConstraintOptimizationEngine()

    def run(
        self,
        risks: List[RiskSignal],
        weather_windows: List[WeatherWindow],
        constraints: FieldConstraints,
    ) -> OptimizedPlan:
        logger.info(f"Running optimization for field: {constraints.field_id}")
        logger.info(f"  Risks received: {len(risks)}")
        logger.info(f"  Budget: ₹{constraints.total_budget_inr} | Water: {constraints.max_water_liters}L")

        candidates = self.action_generator.generate(risks, weather_windows, constraints)
        logger.info(f"  Candidate actions generated: {len(candidates)}")

        plan = self.optimizer.optimize(candidates, constraints)
        logger.info(f"  Plan status: {plan.optimization_status} | Actions: {len(plan.selected_actions)}")
        logger.info(f"  Cost: ₹{plan.total_cost_inr} | Risk Reduction: {plan.expected_risk_reduction:.1%}")

        return plan

    def run_multi_field(
        self,
        risks: List[RiskSignal],
        weather_windows: List[WeatherWindow],
        field_constraints: List[FieldConstraints],
    ) -> List[OptimizedPlan]:
        """Optimize across multiple fields, sorted by priority."""
        sorted_fields = sorted(field_constraints, key=lambda f: f.priority)
        plans = []
        for fc in sorted_fields:
            field_risks = [r for r in risks if r.affected_field_id == fc.field_id]
            field_windows = [w for w in weather_windows if w.field_id == fc.field_id]
            plan = self.run(field_risks, field_windows, fc)
            plans.append(plan)
        return plans
