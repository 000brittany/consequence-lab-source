"""Offline wire fixtures test the adapter, never an agent strategy or provider."""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.calibration import calibration_stop, context_settings, instructions, readiness_report, run_calibration
from consequence_lab.catalogue import manifest
from consequence_lab.clock import ManualClock
from consequence_lab.config import Limits
from consequence_lab.context import build_context
from consequence_lab.provider import HTTPTransport, LunaConfig, ResponsesBackend, decode, prepare_run
from consequence_lab.runner import RunLimitReached, Runner


def reply(tool="act", args=None, *, status="completed", usage=True):
    args = args if args is not None else {"resource": "/schedule", "arguments": {
        "operation": "book", "appointment_id": "p1:A", "room_id": "R0"},
        "disposition": {"on_success": "continue", "on_rejection": "yield_tick"}}
    raw = {"id": "resp_offline", "model": "gpt-6-luna", "status": status, "output": [{"type": "function_call",
           "status": "completed", "call_id": "call_provider", "name": tool, "arguments": json.dumps(args)}]}
    if usage:
        raw["usage"] = {"input_tokens": 2000, "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 500, "cache_write_tokens": 1000},
            "output_tokens_details": {"reasoning_tokens": 60}}
    return json.dumps(raw)


class FixtureTransport:
    network = False

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post(self, endpoint, body, timeout):
        self.requests.append((endpoint, json.loads(body), timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if type(item) is tuple else (200, {"x-request-id": "fixture"}, item)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def runner(self, *responses, **kwargs):
        self.transport = FixtureTransport(responses)
        return prepare_run(manifest(periods=2), backend=ResponsesBackend(transport=self.transport), clock=ManualClock(), **kwargs)

    async def test_exact_wire_choice_result_and_no_controller(self):
        r = self.runner(reply(), reply("write", {"path": "/home/anything", "text": "arbitrary model text", "disposition": "yield_period"}))
        with patch("consequence_lab.recurring_mocks.choose", side_effect=AssertionError("No diagnostic policy in provider execution")):
            first = await r.step()
            second = await r.step()
        self.assertEqual(len(self.transport.requests), 2)
        wire = self.transport.requests[1][1]
        self.assertFalse(wire["store"])
        self.assertEqual(wire["truncation"], "disabled")
        self.assertEqual(wire["reasoning"], {"effort": "medium"})
        self.assertEqual(wire["max_output_tokens"], 8192)
        for forbidden in ("previous_response_id", "conversation", "context_management"):
            self.assertNotIn(forbidden, wire)
        for tool in wire["tools"]:
            self.assertFalse(tool["strict"])
            self.assertIn("disposition", tool["parameters"]["properties"])
        call = next(x for x in wire["input"] if x.get("type") == "function_call")
        self.assertEqual(json.loads(call["arguments"])["disposition"]["on_success"], "continue")
        feedback = next(x for x in wire["input"] if x.get("type") == "function_call_output")
        self.assertEqual(feedback["call_id"], call["call_id"])
        self.assertEqual(json.loads(feedback["output"])["scheduling"]["applied_disposition"], "continue")
        self.assertEqual(first["generation"]["provider_request"]["payload"], self.transport.requests[0][1])
        self.assertEqual(r.state["ledger"]["calls"], 2)
        self.assertEqual(r.state["artifacts"]["/private/A/anything"]["text"], "arbitrary model text")
        verify_experiment(r.records, r.world)

    async def test_cache_accounting_reasoning_not_double_charged(self):
        r = self.runner(reply())
        record = await r.step()
        u = record["accounting"]["usage"]
        self.assertEqual((u["input_tokens"], u["cache_read_tokens"], u["cache_write_tokens"], u["uncached_input_tokens"]), (2000,500,1000,500))
        self.assertEqual((u["output_tokens"], u["reasoning_tokens"], u["visible_output_tokens"]), (100,60,40))
        self.assertEqual(r.state["ledger"]["spend_nano_usd"], 500*100+500*10+1000*125+100*500)
        self.assertIsNone(record["accounting"]["provider"]["provider_invoice_cost"])
        verify_experiment(r.records, r.world)

    async def test_reported_model_id_measurement_hook_and_uncalibrated_contexts(self):
        raw = json.loads(reply())
        raw["model"] = "gpt-6-luna-offline-fixture-only"
        records = []
        for policy in ("generous", "moderate"):
            transport = FixtureTransport([json.dumps(raw)])
            r = prepare_run(manifest(periods=2), policy=policy,
                backend=ResponsesBackend(transport=transport), clock=ManualClock())
            record = await r.step()
            self.assertEqual(record["generation"]["provider_request"]["payload"]["model"], "gpt-6-luna")
            returned = json.loads(record["generation"]["provider_exchange"]["response_text"])
            self.assertEqual(returned["model"], raw["model"])
            self.assertEqual(record["computation"]["internal_activations"]["status"], "not_observed")
            self.assertIsNone(record["computation"]["internal_activations"]["value"])
            self.assertTrue(callable(r.backend.measurements))
            self.assertEqual(record["accounting"]["provider"], r.backend.measurements(
                record["accounting"]["usage"], record["generation"]["provider_exchange"]))
            verify_experiment(r.records, r.world)
            records.append(record)
        # Below both allowances, these settings produce identical requests.
        self.assertEqual(records[0]["generation"]["provider_request"], records[1]["generation"]["provider_request"])
        self.assertEqual(records[0]["registration"]["context_selection"]["included_decisions"], [])
        self.assertEqual(records[1]["registration"]["context_selection"]["excluded_decisions"], [])

    async def test_missing_cache_counters_not_invented(self):
        raw=json.loads(reply()); raw["usage"].pop("input_tokens_details"); raw["usage"].pop("output_tokens_details")
        r=self.runner(json.dumps(raw)); rec=await r.step()
        u=rec["accounting"]["usage"]
        self.assertIsNone(u["cache_write_tokens"]); self.assertIsNone(u["reasoning_tokens"])
        self.assertEqual(r.state["ledger"]["spend_nano_usd"],2000*125+100*500)
        self.assertEqual(rec["accounting"]["provider"]["cost_status"],"conservative_rate_estimate")
        verify_experiment(r.records,r.world)

    async def test_malformed_usage_shapes_are_archivable_failed_decisions(self):
        for key,value in (("input_tokens_details",[1]),("output_tokens_details","bad")):
            raw=json.loads(reply());raw["usage"][key]=value
            r=self.runner(json.dumps(raw));await r.step()
            self.assertEqual(r.state["status"],"paused_provider")
            self.assertGreater(r.state["ledger"]["unknown_reserved_tokens"],0)
            verify_experiment(r.records,r.world)

    async def test_incomplete_output_item_prevents_dispatch_despite_completed_envelope(self):
        raw=json.loads(reply());raw["output"].append({"type":"message","status":"in_progress", "content":[{"type":"output_text","text":"unfinished"}]})
        r=self.runner(json.dumps(raw));rec=await r.step()
        self.assertEqual(r.state["world"]["rooms"],{"R0":None,"R1":None})
        self.assertIsNotNone(rec["computation"]["error"])
        verify_experiment(r.records,r.world)

    async def test_incomplete_malformed_missing_usage_and_wrong_model_stop_without_action(self):
        multiple=json.loads(reply());multiple["output"]*=2
        malformed=json.loads(reply());malformed["output"][0]["arguments"]="{bad"
        wrong=json.loads(reply());wrong["model"]="different-model"
        invalid_usage=json.loads(reply());invalid_usage["usage"]["input_tokens_details"]["cache_write_tokens"]=9999
        for raw in (reply(status="incomplete"),json.dumps(wrong),json.dumps(invalid_usage),reply(usage=False),"not JSON"):
            with self.subTest(raw=raw):
                r=self.runner(raw);rec=await r.step()
                self.assertEqual(r.state["world"]["rooms"],{"R0":None,"R1":None})
                self.assertEqual(r.state["status"],"paused_provider")
                self.assertEqual(rec["generation"]["provider_exchange"]["response_text"],raw)
                self.assertEqual(len(self.transport.requests),1)
                with self.assertRaises(RunLimitReached):await r.step()
                verify_experiment(r.records,r.world)

    async def test_malformed_and_multiple_proposals_continue_with_feedback_without_retry(self):
        multiple=json.loads(reply());multiple["output"]*=2
        malformed=json.loads(reply());malformed["output"][0]["arguments"]="{bad"
        for raw in (json.dumps(multiple),json.dumps(malformed)):
            b=reply(args={"resource":"/schedule","arguments":{"operation":"book","appointment_id":"p1:B","room_id":"R1"},"disposition":"yield_period"})
            a=reply(args={"resource":"/schedule","arguments":{"operation":"book","appointment_id":"p1:A","room_id":"R0"},"disposition":"yield_period"})
            r=self.runner(raw,b,a)
            first=await r.step()
            self.assertEqual(first["consequences"]["tool_response"]["status"],"rejected")
            self.assertEqual(r.state["status"],"running")
            self.assertEqual(calibration_stop(first),"agent_protocol_rejection")
            second=await r.step();third=await r.step()
            self.assertEqual((second["agent"],third["agent"]),("B","A"))
            self.assertEqual(len(self.transport.requests),3)
            self.assertTrue(any('action_error' in x.get('content','') for x in third["generation"]["provider_request"]["payload"]["input"]))
            self.assertEqual(third["consequences"]["tool_response"]["status"],"ok")
            self.assertIsNone(calibration_stop(third))
            verify_experiment(r.records,r.world)

    async def test_rate_limits_and_transport_failures_preserve_reservation_no_retry(self):
        for failure in ((429,{"retry-after":"3"},'{"error":{"message":"rate limit"}}'),TimeoutError("secret must not be logged"),RuntimeError("secret must not be logged")):
            r=self.runner(failure);rec=await r.step()
            self.assertEqual(r.state["status"],"paused_provider")
            self.assertGreater(r.state["ledger"]["unknown_reserved_tokens"],0)
            self.assertGreater(r.state["ledger"]["unknown_reserved_spend_nano_usd"],0)
            self.assertEqual(len(self.transport.requests),1)
            self.assertNotIn("secret must not be logged",str(rec))
            self.assertEqual(rec["generation"]["provider_exchange"]["retry_count"],0)
            if type(failure) is tuple:self.assertGreaterEqual(r.state["ledger"]["not_before_elapsed"],3)
            verify_experiment(r.records,r.world)

    async def test_ordinary_booking_rejection_is_nonfatal(self):
        r=self.runner(reply(args={"resource":"/schedule","arguments":{"operation":"book","appointment_id":"p1:B","room_id":"R0"},"disposition":"continue"}),reply("inspect",{"path":"/home","disposition":"yield_period"}))
        first=await r.step();second=await r.step()
        self.assertEqual(first["consequences"]["tool_response"]["status"],"rejected")
        self.assertEqual(second["agent"],"A")
        self.assertEqual(r.state["status"],"running")
        verify_experiment(r.records,r.world)

    async def test_restore_and_archive_verify_without_network_or_credentials(self):
        with tempfile.TemporaryDirectory() as path:
            r=self.runner(reply(),archive=path);await r.step()
            r.checkpoint(Path(path)/"checkpoint.json")
            self.assertEqual(len(Archive.read(path)),len(r.records))
            restored=Runner.restore(r.checkpoint())
            self.assertEqual(restored.state,r.state)
            with self.assertRaises(PermissionError):await restored.step()
            self.assertEqual(restored.state["ledger"]["calls"],1)
            self.assertNotIn("OPENAI_API_KEY",json.dumps(r.checkpoint()))
            continuation=Runner.restore(r.checkpoint(),provider_transport=FixtureTransport([reply("read",{"path":"/notices/R0/current","disposition":"yield_period"})]))
            await continuation.step(); verify_experiment(continuation.records,continuation.world)

    async def test_wire_normalization_and_usage_mutations_are_rejected(self):
        r=self.runner(reply());await r.step()
        for field in ("wire","usage","arguments"):
            rec=copy.deepcopy(r.records);last=rec[-1]
            if field=="wire":last["generation"]["provider_request"]["payload"]["reasoning"]["effort"]="none"
            elif field=="usage":last["computation"]["response"]["usage"]["cache_read_tokens"]+=1
            else:last["computation"]["response"]["actions"][0]["arguments"]["disposition"]="yield_period"
            with self.assertRaises(ValueError):verify_experiment(rec,r.world)

    async def test_limits_reserve_worst_case_before_any_provider_attempt(self):
        for limits in (Limits(calls=1,total_tokens=1000,spend_nano_usd=1_000_000_000,output_tokens=8192,input_tokens=65536),
                       Limits(calls=1,total_tokens=3_000_000,spend_nano_usd=1,output_tokens=8192,input_tokens=65536)):
            r=self.runner(reply(),limits=limits)
            with self.assertRaises(RunLimitReached):await r.step()
            self.assertEqual(self.transport.requests,[])
            self.assertEqual(r.state["ledger"]["calls"],0)
            verify_experiment(r.records,r.world)

    async def test_no_implicit_transport_or_other_model_profile(self):
        r=prepare_run(manifest())
        with self.assertRaises(PermissionError):await r.step()
        self.assertEqual(r.state["ledger"]["calls"],0)
        with self.assertRaises(PermissionError):HTTPTransport()
        with self.assertRaises(ValueError):LunaConfig(model="guessed-model")

    async def test_actual_usage_violation_preserves_usage_without_domain_effect(self):
        raw=json.loads(reply());raw["usage"]["output_tokens"]=8193
        r=self.runner(json.dumps(raw));rec=await r.step()
        self.assertEqual(rec["computation"]["error"]["type"],"UsageViolation")
        self.assertEqual(r.state["status"],"paused_limit")
        self.assertEqual(r.state["ledger"]["output_tokens"],8193)
        self.assertEqual(r.state["world"]["rooms"],{"R0":None,"R1":None})
        verify_experiment(r.records,r.world)

    async def test_duplicate_json_keys_are_rejected_with_exact_raw_response(self):
        raw=json.loads(reply());raw["output"][0]["arguments"]='{"path":"/home/x","path":"/home/y"}'
        r=self.runner(json.dumps(raw));rec=await r.step()
        self.assertEqual(rec["computation"]["response"]["finish_reason"],"stop")
        self.assertEqual(rec["consequences"]["tool_response"]["status"],"rejected")
        self.assertEqual(r.state["status"],"running")
        self.assertEqual(calibration_stop(rec),"agent_protocol_rejection")
        self.assertEqual(rec["generation"]["provider_exchange"]["response_text"],json.dumps(raw))
        verify_experiment(r.records,r.world)

    async def test_nonfinite_numeric_proposal_is_a_serializable_nonfatal_rejection(self):
        raw=json.loads(reply());raw["output"][0]["arguments"]='{"offset":1e999}'
        r=self.runner(json.dumps(raw));rec=await r.step()
        self.assertEqual(r.state["status"],"running")
        self.assertEqual(rec["consequences"]["tool_response"]["status"],"rejected")
        self.assertEqual(rec["generation"]["provider_exchange"]["response_text"],json.dumps(raw))
        verify_experiment(r.records,r.world)

    async def test_provider_timeout_records_one_attempt_and_stops(self):
        class Slow(FixtureTransport):
            async def post(self,*args):
                self.requests.append(args)
                await asyncio.sleep(.1)
        transport=Slow([])
        r=prepare_run(manifest(),backend=ResponsesBackend(LunaConfig(timeout_seconds=.001),transport),clock=ManualClock())
        rec=await r.step()
        self.assertEqual(len(transport.requests),1)
        self.assertEqual(rec["generation"]["provider_exchange"]["failure"],"TimeoutError")
        self.assertEqual(r.state["status"],"paused_provider")
        verify_experiment(r.records,r.world)

    async def test_yielded_completed_actor_has_no_compulsory_provider_calls(self):
        raw=json.loads(reply());args=json.loads(raw["output"][0]["arguments"]);args["disposition"]="yield_period"
        raw["output"][0]["arguments"]=json.dumps(args)
        transport=FixtureTransport([json.dumps(raw)])
        r=prepare_run(manifest(order=("A",),periods=1),backend=ResponsesBackend(transport=transport),clock=ManualClock())
        await r.run(8)
        self.assertEqual(len(transport.requests),1)
        self.assertEqual(r.state["ledger"]["calls"],1)
        self.assertEqual(r.state["status"],"finished")
        self.assertGreater(r.state["world"]["control"]["ticks_advanced"],0)
        verify_experiment(r.records,r.world)

    async def test_hidden_worlds_have_identical_authorized_provider_requests(self):
        requests=[]
        for mask in (None,"R0","R1"):
            transport=FixtureTransport([reply()])
            r=prepare_run(manifest(mask=mask),backend=ResponsesBackend(transport=transport),clock=ManualClock())
            await r.step();requests.append(transport.requests[0][1])
            verify_experiment(r.records,r.world)
        self.assertEqual(requests[0],requests[1]);self.assertEqual(requests[1],requests[2])

    async def test_context_history_is_not_automatically_summarized_or_retrieved(self):
        r=self.runner(reply());state=r.state
        state["agents"]["A"]["history"]=[{"decision_id":f"old-{i}","messages":[{"role":"user","content":str(i)+"x"*1200}],"retrieved_artifacts":[]} for i in range(30)]
        for name in ("generous","moderate","short_reference"):
            chosen=context_settings(name);state["config"]["recent_cycles"]=chosen["recent_cycles"]
            state["config"]["limits"]["input_tokens"]=chosen["limits"].input_tokens
            _,ctx=build_context(state,"A",{},r.backend)
            self.assertFalse(ctx["automatic_summary"])
            self.assertEqual(ctx["supplied_artifacts"],[])
            if name=="generous":self.assertEqual(len(ctx["included_decisions"]),30)
            if name=="moderate":self.assertTrue(any(x["reason"]=="input_budget" for x in ctx["excluded_decisions"]))
            if name=="short_reference":self.assertEqual(len(ctx["included_decisions"]),6)
        self.assertEqual(len(state["agents"]["A"]["history"]),30)
        self.assertNotIn("/volumes",str(instructions()))

    async def test_readiness_report_is_pure_and_budgeted(self):
        report=readiness_report()
        self.assertEqual(report["provider_calls"],0)
        self.assertEqual(report["calibration_plan"]["maximum_generations_total"],16)
        self.assertEqual(len(report["settings"]),3)
        self.assertEqual(report,readiness_report())

    async def test_calibration_stops_at_invalid_proposal_without_retry_and_finalizes(self):
        raw=json.loads(reply());raw["output"]*=2
        with tempfile.TemporaryDirectory() as path:
            r=self.runner(json.dumps(raw),reply(),archive=path)
            report=await run_calibration(r)
            self.assertEqual(report["reason"],"agent_protocol_rejection")
            self.assertEqual(report["ledger"]["calls"],1)
            self.assertEqual(r.state["status"],"running")
            self.assertEqual(len(self.transport.requests),1)
            self.assertTrue((Path(path)/"calibration.json").exists())
            verify_experiment(Archive.read(path),r.world)

    async def test_network_execution_requires_authorization_credentials_and_archive(self):
        class NetworkFixture(FixtureTransport):
            network=True
        transport=NetworkFixture([reply()])
        for allowed,key in ((False,True),(True,False),(True,True)):
            with patch.dict(os.environ,{"OPENAI_API_KEY":"fixture"} if key else {},clear=True):
                r=prepare_run(manifest(),backend=ResponsesBackend(transport=transport),clock=ManualClock(),allow_provider_calls=allowed)
                with self.assertRaises(PermissionError):await r.step()
                self.assertEqual(r.state["ledger"]["calls"],0)
        self.assertEqual(transport.requests,[])


if importlib.util.find_spec("httpx") is None and os.environ.get("EIG_REQUIRE_PROVIDER")=="1":
    raise RuntimeError("Required provider HTTP dependency is missing")


@unittest.skipUnless(importlib.util.find_spec("httpx"),"Optional provider HTTP dependency")
class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_http_boundary_without_external_requests_or_retries(self):
        import httpx
        requests=[]
        def respond(request):
            requests.append(request)
            self.assertEqual(request.headers["authorization"],"Bearer fixture-not-a-real-key")
            return httpx.Response(429,json={"error":{"message":"limit"}},headers={"retry-after":"2","authorization":"never archive this"})
        with patch.dict(os.environ,{"OPENAI_API_KEY":"fixture-not-a-real-key"}):
            transport=HTTPTransport(allow_provider_calls=True,http_transport=httpx.MockTransport(respond))
            code,headers,body=await transport.post("https://api.openai.com/v1/responses",b'{}',1)
        self.assertEqual(len(requests),1);self.assertEqual(code,429)
        self.assertEqual(headers,{"retry-after":"2"})
        self.assertNotIn("fixture-not-a-real-key",body)
