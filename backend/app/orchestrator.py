"""Central orchestrator.

Responsibilities (and only these): pick the relevant agents for the request,
gather the data they need once, run them in dependency order with independent
agents in parallel, hand everything to Agent 15 for validation, and then ask the
LLM to put the validated findings into the farmer's language.

It is not one of the 15 agricultural agents, and it never invents agronomy: if
the agents could not produce findings, the answer says so.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set

from .agents import registry
from .agents.base import AgentContext, digest
from .conversation import intent as intent_module
from .conversation import manager
from .core import languages, storage
from .core import ledger as ledger_store
from .core.schemas import (Advisory, AgentResult, FarmContext, RetrievedChunk,
                           SensorReading, Status)
from .llm.mistral_client import (build_answer_messages, clean_answer,
                                 client as llm)
from .rag import pipeline as rag
from .sensors import validation as sensor_validation
from .sensors import source as sensor_source
from .services import market as market_service
from .services import soilgrids as soil_service
from .services import support as support_service
from .services import weather as weather_service

# Which external services each intent is allowed to call. Fetching everything for
# every question would be slow and would spend quota for nothing.
# One slow provider must not hold a fifteen-agent run. Comfortably longer than any
# single service timeout, short enough that the page is not left waiting.
GATHER_DEADLINE_SECONDS = 25

WEATHER_INTENTS = {"weather", "irrigation", "crop_selection", "pest_disease", "seed_sowing",
                   "crop_protection", "crop_stage", "general"}
MARKET_INTENTS = {"market", "economics", "finance"}

# Schemes, credit terms and helplines are local reference data - no network, no
# latency - so they are attached whenever the question is anywhere near them, and on
# a full run always. The conversational and voice replies read them from context.
SUPPORT_INTENTS = {"scheme", "finance", "support", "economics"}

# The farm's own money record, attached when the question is about it so the chat
# answers "how much have I spent" from the ledger rather than guessing.
LEDGER_INTENTS = {"ledger", "economics", "finance"}
SOILGRIDS_INTENTS = {"soil_health", "crop_selection", "nutrient"}

# The agent that owns each intent. Used to lead the rule-engine answer with the
# finding the farmer actually asked about.
PRIMARY_AGENT = {
    "soil_health": 2, "nutrient": 6, "irrigation": 7, "crop_selection": 3,
    "crop_stage": 4, "weather": 5, "pest_disease": 8, "weed": 9,
    "seed_sowing": 10, "crop_protection": 11, "market": 12, "economics": 13,
    "residue": 14, "sensor": 1, "general": 2,
}


@dataclass
class _Prepared:
    """What ``run`` and ``stream`` both need before the answer is written."""

    request_id: str
    started: float
    detection: Any
    ctx: AgentContext
    results: List[AgentResult]
    gather_ms: int
    agents_ms: int
    report: Any
    session: Any
    advisory: Optional[Advisory] = None


class Orchestrator:
    async def run(self, question: str, session: manager.Session, *,
                  use_rag: bool = True, run_agents: bool = True,
                  force_all: bool = False, force_intent: Optional[str] = None,
                  extra_entities: Optional[Dict[str, Any]] = None,
                  progress: Optional[Any] = None) -> Advisory:
        """Answer one request.

        ``force_all`` runs the complete 15-agent chain instead of the agents the
        intent selects. That is the "Run full pipeline" button: slower and more
        expensive, but it exercises and shows every agent at once.
        """
        prepared = await self._prepare(
            question, session, use_rag=use_rag, run_agents=run_agents,
            force_all=force_all, force_intent=force_intent,
            extra_entities=extra_entities, progress=progress)
        if prepared.advisory is not None:                 # small talk: already answered
            return prepared.advisory

        phase = time.perf_counter()
        prepared.report("answering")
        answer, source, model_used, notices = await self._compose(
            question, session, prepared.ctx, prepared.results, use_rag)
        compose_ms = int((time.perf_counter() - phase) * 1000)
        return self._finish(prepared, answer, source, model_used, notices, compose_ms)

    # ------------------------------------------------------------- streaming
    async def stream(self, question: str, session: manager.Session, *,
                     use_rag: bool = True, run_agents: bool = True,
                     force_all: bool = False, force_intent: Optional[str] = None,
                     extra_entities: Optional[Dict[str, Any]] = None):
        """The same run, with the answer handed over as it is written.

        Yields dicts. ``{"stage": ...}`` as each phase starts, ``{"delta": ...}``
        for each piece of the answer, and finally ``{"advisory": {...}}`` with the
        whole validated result - the same object ``run`` returns, so a caller that
        wants the agent trace still gets it.

        Why this exists: on this deployment a dashboard answer takes about 3 s, of
        which roughly 2 s is the model writing prose that is already decided by the
        time the agents have finished. The first words arrive in about 400 ms. The
        work is not faster; the farmer simply stops waiting for it in silence.
        """
        events: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        def report(event: Dict[str, Any]) -> None:
            # _prepare's own `report` has already built the dict, so this takes it
            # as it is. Re-wrapping it produced {"stage": {"stage": "routing"}},
            # which a page reading event.stage renders as [object Object].
            events.put_nowait(dict(event))

        # The stages are drained WHILE the run happens, not after it. Collecting
        # them first and sending them at the end would show a finished diagram the
        # moment the answer arrived, which tells the farmer nothing about the wait.
        work = asyncio.ensure_future(self._prepare(
            question, session, use_rag=use_rag, run_agents=run_agents,
            force_all=force_all, force_intent=force_intent,
            extra_entities=extra_entities, progress=report))
        while True:
            pending = asyncio.ensure_future(events.get())
            done, _ = await asyncio.wait({pending, work},
                                         return_when=asyncio.FIRST_COMPLETED)
            if pending in done:
                yield pending.result()
                continue
            pending.cancel()
            break
        prepared = await work
        while not events.empty():
            yield events.get_nowait()

        if prepared.advisory is not None:
            # A greeting has no model call to stream. Send it as one piece so the
            # page does not have to care which kind of answer it asked for.
            yield {"delta": prepared.advisory.answer}
            yield {"advisory": prepared.advisory.model_dump(mode="json")}
            return

        yield {"stage": "answering"}
        phase = time.perf_counter()
        pieces: List[str] = []
        source, model_used = "", ""
        notices: List[str] = []
        streamed = False

        usable = [r for r in prepared.results
                  if r.status in (Status.OK, Status.NEEDS_INPUT)]
        if llm.configured():
            messages = build_answer_messages(
                question, session.language, digest(usable),
                rag.to_prompt_block(prepared.ctx.retrieved) if use_rag else "",
                manager.history_for_prompt(session), farm=session.farm,
                support=prepared.ctx.support)
            async for event in llm.stream(messages, temperature=0.2, max_tokens=600):
                if "delta" in event:
                    pieces.append(event["delta"])
                    yield {"delta": event["delta"]}
                elif event.get("done"):
                    streamed = True
                    source, model_used = "llm", str(event.get("model", ""))
                    if event.get("truncated"):
                        notices.append("The answer was cut short: %s"
                                       % str(event.get("error", ""))[:120])
                elif event.get("error"):
                    notices.append("Generated answer unavailable (%s). Showing the "
                                   "structured findings instead."
                                   % str(event["error"])[:160])

        if streamed and pieces:
            # The same cleaner the non-streaming path uses. The pieces already went
            # out raw, so the page is told to replace what it has rather than append.
            answer = clean_answer("".join(pieces))
            if answer != "".join(pieces):
                yield {"replace": answer}
        else:
            if not llm.configured():
                notices.append("Mistral API key is not configured, so the reply is "
                               "assembled from the agent findings rather than written "
                               "by the language model.")
            answer = self._rule_answer(prepared.ctx, prepared.results)
            source, model_used = "rule_engine", ""
            yield {"replace": answer}

        compose_ms = int((time.perf_counter() - phase) * 1000)
        advisory = self._finish(prepared, answer, source, model_used, notices, compose_ms)
        yield {"advisory": advisory.model_dump(mode="json")}

    # ------------------------------------------------------- shared machinery
    async def _prepare(self, question: str, session: manager.Session, *,
                       use_rag: bool, run_agents: bool, force_all: bool,
                       force_intent: Optional[str],
                       extra_entities: Optional[Dict[str, Any]],
                       progress: Optional[Any]) -> "_Prepared":
        """Everything up to the answer: routing, gathering, the agents.

        Shared by ``run`` and ``stream`` so the two cannot drift apart - the only
        difference between them is how the prose comes back.
        """
        started = time.perf_counter()
        request_id = "adv-" + uuid.uuid4().hex[:12]

        def report(stage: str, **extra: Any) -> None:
            """Fire and forget: a progress callback must never break the run."""
            if progress is None:
                return
            try:
                progress({"stage": stage, **extra})
            except Exception:                               # noqa: BLE001
                pass

        report("routing")
        detection = manager.resolve_turn(session, question)
        if extra_entities:
            detection.entities.update(extra_entities)
        if force_intent and force_intent in intent_module.INTENTS:
            # Some channels know the intent better than the words do: a photo upload
            # is a pest question even when the farmer typed "leaves look patchy",
            # which no keyword list catches.
            spec = intent_module.INTENTS[force_intent]
            detection.intent = force_intent
            detection.agents = list(spec["agents"])             # type: ignore[arg-type]
            detection.topics = list(spec["topics"])             # type: ignore[arg-type]
            detection.confidence = max(detection.confidence, 0.9)

        ctx = AgentContext(
            question=question,
            intent=detection.intent,
            language=session.language,
            entities=dict(detection.entities),
            farm=session.farm,
        )

        if force_all:
            ctx.intent = detection.intent = detection.intent or "general"

        # A greeting is answered as a greeting. No agents, no retrieval and no
        # model call: there is nothing to reason about, and the chain answered
        # "hello" with a soil reading and a question about the sowing date.
        if detection.intent == "small_talk" and not force_all:
            return _Prepared(
                request_id=request_id, started=started, detection=detection, ctx=ctx,
                results=[], gather_ms=0, agents_ms=0, report=report, session=session,
                advisory=self._small_talk_reply(question, session, detection, started))

        selected = self._select_agents(detection, run_agents, force_all=force_all)

        # Phase timings: on a full run almost all the wall clock is data fetching and
        # the model, not the agents, and the dashboard should be able to show that.
        phase = time.perf_counter()
        report("gathering", selected=list(selected))
        audience = "call" if getattr(session, "channel", "") == "call" else "chat"
        await self._gather(ctx, detection, selected, use_rag, force_all=force_all,
                           audience=audience)
        gather_ms = int((time.perf_counter() - phase) * 1000)

        results: List[AgentResult] = []
        phase = time.perf_counter()
        if run_agents:
            results = await self._execute(ctx, selected, report)
        agents_ms = int((time.perf_counter() - phase) * 1000)

        return _Prepared(request_id=request_id, started=started, detection=detection,
                         ctx=ctx, results=results, gather_ms=gather_ms,
                         agents_ms=agents_ms, report=report, session=session)

    def _finish(self, prepared: "_Prepared", answer: str, source: str, model_used: str,
                notices: List[str], compose_ms: int) -> Advisory:
        """Assemble, persist and return the advisory. One place, both paths."""
        ctx, session = prepared.ctx, prepared.session
        advisory = Advisory(
            request_id=prepared.request_id,
            session_id=session.session_id,
            question=prepared.ctx.question,
            language=session.language,
            intent=prepared.detection.intent,
            answer=answer,
            answer_source=source,
            answer_model=model_used,
            agents_invoked=[r.agent_id for r in prepared.results],
            agent_results=prepared.results,
            retrieved=ctx.retrieved,
            validation=(ctx.result(15).data if ctx.result(15) else {}),
            follow_up_questions=self._follow_ups(ctx, prepared.detection, ctx.question),
            warnings=self._warnings(ctx, prepared.results, notices),
            provenance_summary=self._provenance(prepared.results),
            timings={"gather_ms": prepared.gather_ms, "agents_ms": prepared.agents_ms,
                     "answer_ms": compose_ms},
            duration_ms=int((time.perf_counter() - prepared.started) * 1000),
        )
        storage.save_advisory(advisory.model_dump(mode="json"))
        manager.update_memory(session, prepared.detection)
        manager.save(session)
        return advisory

    # ----------------------------------------------------------- small talk
    def _small_talk_reply(self, question: str, session: manager.Session,
                          detection: intent_module.IntentResult,
                          started: float) -> Advisory:
        """Answer a greeting as a greeting.

        No agents, no retrieval, no model call. The chain has nothing to reason
        about here, and running it anyway is what answered "hello" with a soil
        moisture figure and a question about the sowing date.

        The reply names what Saarthi can actually do, because the person who
        just typed "hi" is usually about to ask something and does not yet know
        what is on offer.
        """
        kind = str(detection.entities.get("small_talk") or "greeting")
        farm = session.farm
        name = (getattr(farm, "farmer_name", "") or "").strip()
        crop = (getattr(farm, "crop", "") or "").strip()

        hello = "Namaste%s." % (", " + name.split()[0] if name else "")
        offer = ("You can ask me about your soil, the weather, when to irrigate, "
                 "fertiliser, pests, or today's mandi price.")
        if crop:
            offer = ("You can ask me about your %s - the soil, the weather, when to "
                     "irrigate, fertiliser, pests, or today's mandi price." % crop)

        if kind == "thanks":
            answer = "You are welcome. Ask me whenever you need something."
        elif kind == "farewell":
            answer = "Theek hai. I am here whenever you need me."
        elif kind == "about":
            answer = ("I am Saarthi, your farming companion. " + offer +
                      " I can also ring you and talk it through in your own language.")
        else:
            answer = hello + " " + offer

        return Advisory(
            request_id=uuid.uuid4().hex,
            session_id=session.session_id,
            question=question,
            language=session.language,
            intent="small_talk",
            answer=answer,
            # Not the rule engine and not the model: this is a fixed courtesy,
            # and labelling it honestly keeps the provenance counts truthful.
            answer_source="small_talk",
            answer_model="",
            agents_invoked=[],
            agent_results=[],
            retrieved=[],
            validation={},
            follow_up_questions=[],
            warnings=[],
            provenance_summary={},
            timings={"gather_ms": 0, "agents_ms": 0, "answer_ms": 0},
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # ------------------------------------------------------------ selection
    def _select_agents(self, detection: intent_module.IntentResult,
                       run_agents: bool, force_all: bool = False) -> List[int]:
        if not run_agents:
            return []
        if force_all:
            return sorted(registry.AGENTS)
        selected: Set[int] = set(detection.agents or [15])
        selected.add(15)                                   # validation always runs
        # Pull in declared dependencies of everything selected.
        changed = True
        while changed:
            changed = False
            for agent_id in list(selected):
                for dependency in registry.get(agent_id).depends_on:
                    if dependency != 15 and dependency not in selected and agent_id != 15:
                        selected.add(dependency)
                        changed = True
        return sorted(selected)

    # -------------------------------------------------------------- gather
    async def _gather(self, ctx: AgentContext, detection: intent_module.IntentResult,
                      selected: Sequence[int], use_rag: bool,
                      force_all: bool = False, audience: str = "chat") -> None:
        tasks: Dict[str, asyncio.Task] = {}
        farm = ctx.farm

        if use_rag:
            query = detection.normalised_query or ctx.question
            # A full run is not about one topic, so do not narrow retrieval to one.
            topics = None if force_all else detection.topics
            # The audience is an access rule: a document a person marked for the
            # phone line only must not reach a dashboard answer, and the other way
            # round. The channel decides it, not the caller.
            tasks["rag"] = asyncio.ensure_future(
                rag.retrieve(query, top_k=8 if force_all else 6, topics=topics,
                             audience=audience))
        if force_all or (detection.intent in WEATHER_INTENTS
                         and ({5, 7, 8, 10, 11} & set(selected))):
            tasks["weather"] = asyncio.ensure_future(
                weather_service.get_summary(farm.latitude, farm.longitude))
        if force_all or (detection.intent in SOILGRIDS_INTENTS and (2 in selected)):
            tasks["soil"] = asyncio.ensure_future(
                soil_service.get_summary(farm.latitude, farm.longitude))
        if 12 in selected:
            commodity = ctx.entities.get("crop") or farm.crop or "wheat"
            tasks["market"] = asyncio.ensure_future(
                market_service.get_summary(commodity, farm.state))

        if force_all or detection.intent in SUPPORT_INTENTS:
            ctx.support = support_service.summary_for_agents(
                ctx.farm.model_dump(mode="json"))

        if force_all or detection.intent in LEDGER_INTENTS:
            phone = str(getattr(ctx.farm, "phone", "") or "")
            if phone:
                ctx.ledger = ledger_store.summary(phone)

        # Sensor reading comes from storage, not the network.
        self._load_reading(ctx)

        if tasks:
            try:
                done = await asyncio.wait_for(
                    asyncio.gather(*tasks.values(), return_exceptions=True),
                    timeout=GATHER_DEADLINE_SECONDS)
            except asyncio.TimeoutError:
                # Take whatever finished and move on. A lookup still running is
                # cancelled rather than left to hold a connection open.
                done = []
                for key, task in tasks.items():
                    if task.done() and not task.cancelled():
                        exc = task.exception()
                        done.append(exc if exc else task.result())
                    else:
                        task.cancel()
                        done.append(TimeoutError(
                            "%s did not answer within %d s" % (key, GATHER_DEADLINE_SECONDS)))
                ctx.notes.append(
                    "Some lookups passed %d seconds and were dropped; the agents that "
                    "needed them will say so." % GATHER_DEADLINE_SECONDS)

            for key, value in zip(tasks.keys(), done):
                if isinstance(value, BaseException):
                    ctx.notes.append("%s lookup failed: %s" % (key, str(value)[:120]))
                    continue
                if key == "rag":
                    ctx.retrieved = list(value)             # type: ignore[arg-type]
                elif key == "weather":
                    ctx.weather = value                     # type: ignore[assignment]
                elif key == "soil":
                    ctx.soilgrids = value                   # type: ignore[assignment]
                elif key == "market":
                    ctx.market = value                      # type: ignore[assignment]

    def _load_reading(self, ctx: AgentContext) -> None:
        """The reading the agents reason over.

        Same source as the dashboard, so the chain cannot be looking at one set of
        numbers while the Soil Health page shows another. The farm plot is tried
        first; if nothing has been filed against it, the newest reading in the
        active mode is used, because a farmer with one probe and one dashboard
        means "my sensor" rather than "the row tagged field-1".
        """
        payload, meta = sensor_source.current(ctx.farm.parcel_id)
        if not payload:
            payload, meta = sensor_source.current()
        if not payload:
            if meta.get("note"):
                ctx.notes.append(meta["note"])
            return
        try:
            checked = sensor_validation.validate(dict(payload))
        except Exception:                                   # noqa: BLE001
            return
        ctx.reading = checked["reading"]
        ctx.reading_validation = checked["validation"]

    # ------------------------------------------------------------- execute
    async def _execute(self, ctx: AgentContext, selected: Sequence[int],
                       report: Optional[Any] = None) -> List[AgentResult]:
        """Run agents in dependency waves; independent agents run concurrently."""
        pending = [a for a in selected if a != 15]
        completed: Set[int] = set()
        ordered: List[AgentResult] = []

        while pending:
            wave = [a for a in pending
                    if all(d in completed or d not in selected
                           for d in registry.get(a).depends_on)]
            if not wave:                                    # cycle guard
                wave = list(pending)
            if report:
                report("agents", running=sorted(wave))
            results = await asyncio.gather(*[registry.get(a).run(ctx) for a in wave])
            for agent_id, result in zip(wave, results):
                ctx.results[agent_id] = result
                ordered.append(result)
                completed.add(agent_id)
                pending.remove(agent_id)
            if report:
                report("agents", done=sorted(completed),
                       statuses={r.agent_name and a: ctx.results[a].status.value
                                 for a, r in ctx.results.items()})

        if 15 in selected:
            if report:
                report("agents", running=[15])
            validation = await registry.get(15).run(ctx)
            ctx.results[15] = validation
            ordered.append(validation)
            if report:
                report("agents", done=sorted(ctx.results))
        return ordered

    # ------------------------------------------------------------- compose
    async def _compose(self, question: str, session: manager.Session, ctx: AgentContext,
                       results: List[AgentResult],
                       use_rag: bool) -> tuple[str, str, str, List[str]]:
        notices: List[str] = []
        usable = [r for r in results if r.status in (Status.OK, Status.NEEDS_INPUT)]
        agent_digest = digest(usable)
        knowledge = rag.to_prompt_block(ctx.retrieved) if use_rag else ""

        if llm.configured():
            messages = build_answer_messages(
                question, session.language, agent_digest, knowledge,
                manager.history_for_prompt(session), farm=session.farm,
                support=ctx.support)
            result = await llm.chat(messages, temperature=0.2, max_tokens=600)
            if result.ok and result.text:
                # Strip markdown: the same answer may be spoken over a phone call.
                return clean_answer(result.text), "llm", result.model, notices
            notices.append("Generated answer unavailable (%s). Showing the structured "
                           "findings instead." % result.unavailable_notice)
        else:
            notices.append("Mistral API key is not configured, so the reply is assembled "
                           "from the agent findings rather than written by the language "
                           "model. Add MISTRAL_API_KEY in config/api_keys.env.")
            if session.language != "en":
                notices.append("Translation into %s needs the language model; this reply is "
                               "in English." % languages.resolve(session.language).english_name)
        return self._rule_answer(ctx, results), "rule_engine", "", notices

    def _rule_answer(self, ctx: AgentContext, results: List[AgentResult]) -> str:
        """Deterministic fallback: readable prose assembled from agent output.

        It leads with the agent that owns the question rather than reciting every
        agent, so the reply stays readable without the LLM.
        """
        if not results:
            return ("I did not run the agricultural agents for this message. Ask me about "
                    "soil, irrigation, nutrients, weather, pests, weeds, seed, market "
                    "prices or crop residue and I will.")
        primary_id = PRIMARY_AGENT.get(ctx.intent)
        usable = [r for r in results
                  if r.agent_id != 15 and r.status != Status.UNAVAILABLE]
        primary = next((r for r in usable if r.agent_id == primary_id), None)
        ordered = ([primary] if primary else []) + [r for r in usable if r is not primary]

        lines: List[str] = []
        for index, result in enumerate(ordered):
            detail = 3 if index == 0 else 1
            if result.headline:
                lines.append(result.headline)
            lines.extend(result.findings[:detail])
            if result.actions and index == 0:
                lines.append("What to do: " + result.actions[0])
            if index >= 2:
                break
        validation = ctx.result(15)
        if validation:
            for warning in validation.warnings[:3]:
                lines.append("Note: " + warning)
            if validation.questions:
                lines.append("To sharpen this: " + validation.questions[0])
        if not lines:
            missing = sorted({m for r in results for m in r.missing_inputs})
            return ("I could not answer this with the information available. Missing: "
                    + ", ".join(missing) + "." if missing else
                    "I could not answer this with the information available.")
        return " ".join(lines)

    # -------------------------------------------------------------- extras
    def _follow_ups(self, ctx: AgentContext, detection: intent_module.IntentResult,
                    question: str) -> List[str]:
        questions: List[str] = []
        clarify = intent_module.needs_clarification(detection, question)
        if clarify:
            questions.append(clarify)
        for result in ctx.results.values():
            questions.extend(result.questions)
        seen: List[str] = []
        for item in questions:
            if item and item not in seen:
                seen.append(item)
        return seen[:3]

    def _warnings(self, ctx: AgentContext, results: List[AgentResult],
                  notices: List[str]) -> List[str]:
        warnings = list(notices)
        validation = ctx.result(15)
        if validation:
            warnings.extend(validation.warnings)
        else:
            for result in results:
                warnings.extend(result.warnings)
        warnings.extend(ctx.notes)
        seen: List[str] = []
        for item in warnings:
            if item and item not in seen:
                seen.append(item)
        return seen

    def _provenance(self, results: List[AgentResult]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for result in results:
            for provenance in result.provenance:
                counts[provenance.value] = counts.get(provenance.value, 0) + 1
        return counts


orchestrator = Orchestrator()
