"""No-cost Rooms v3 continuation, quality-flag, recovery, and replay regressions."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from consequence_lab.archive import Archive
from consequence_lab.audit import verify_experiment
from consequence_lab.backends import ScriptedMock
from consequence_lab.calibration import run_calibration
from consequence_lab.clock import ManualClock
from consequence_lab.provider import ResponsesBackend, retry_after_delay
from consequence_lab.rooms_v3_manifest import generate
from consequence_lab.rooms_v3_reference import verify_domain
from consequence_lab.rooms_v3_run import new_run, limits
from consequence_lab.runner import Runner, RunLimitReached
from tools.rooms_v3_analysis import analyze
from tools.rooms_v3_branches import prepare, verify_origin, replay_recorded
from tools.rooms_v3_validation import action


def completed():
    params = {"resource": "/opportunities", "arguments": {"operation": "yield"},
              "disposition": "yield_tick"}
    return json.dumps({"model": "gpt-6-luna", "status": "completed",
        "output": [{"type": "function_call", "status": "completed",
                    "call_id": "call-real", "name": "act", "arguments": json.dumps(params)}],
        "usage": {"input_tokens": 2000, "output_tokens": 100,
                  "input_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 100},
                  "output_tokens_details": {"reasoning_tokens": 50}}})


class FixtureTransport:
    network = False

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def post(self, endpoint, body, timeout):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, tuple) else (200, {"x-request-id": "test"}, item)


def missing(status, headers=None):
    return (status, headers or {}, '{"error":{"message":"transient"}}')


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    def make(self, root, responses, *, calls=12):
        transport = FixtureTransport(responses)
        runner = new_run(generate(0, 4), ResponsesBackend(transport=transport),
                         archive=root, clock=ManualClock(),
                         cap=replace(limits(4), calls=calls))
        return runner, transport

    async def test_three_missed_then_next_agent_and_ordinary_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "research"
            runner, transport = self.make(folder, [missing(429, {"retry-after": "3"}),
                                                   missing(503, {'retry-after': '5'}), TimeoutError("secret transport diagnostic"),
                                                   completed(), completed()], calls=5)
            records = [await runner.step() for _ in range(5)]
            self.assertEqual(transport.calls, 5)
            self.assertEqual([r["agent"] for r in records], ["A", "B", "C", "A", "B"])
            self.assertEqual([r["computation"]["classification"] for r in records[:3]],
                             ["unobserved_generation"] * 3)
            self.assertEqual([r["consequences"]["scheduling"]["basis"] for r in records[:3]],
                             ["platform_missed_decision"] * 3)
            self.assertEqual(runner.state["status"], "running")
            self.assertGreaterEqual(runner.state["ledger"]["elapsed_seconds"], 8)
            self.assertEqual([r["computation"]["selected_action"] for r in records[:3]], [None] * 3)
            for r in records[:3]:
                history = r["state_after"]["agents"][r["agent"]]["history"][-1]
                self.assertEqual([m["role"] for m in history["messages"]], ["user"])
                self.assertEqual(r["accounting"]["usage"], None)
                self.assertNotIn("secret transport diagnostic", str(r))
            self.assertEqual(records[3]["registration"]["observation"]["previous_decision_outcome"]["classification"],
                             "unobserved_generation")
            self.assertEqual(records[3]["registration"]["observation"]["previous_decision_outcome"]["http_status"], 429)
            self.assertEqual(records[3]["computation"]["classification"], "valid_action")
            quality = runner.state["ledger"]["technical"]
            self.assertTrue(quality["compromised"])
            self.assertEqual(quality["first_trigger_decision"], "decision-00000002")
            self.assertEqual(quality["per_agent_unobserved"], {"A": 1, "B": 1, "C": 1})
            self.assertEqual(quality["total_unobserved"], 3)
            self.assertEqual(quality["consecutive_unobserved"], 0)
            self.assertIn("three_consecutive", quality["trigger_reasons"])
            self.assertEqual(runner.state["ledger"]["unknown_reserved_tokens"], 3 * 1_050_000)
            self.assertEqual(runner.state["ledger"]["unknown_reserved_spend_nano_usd"], 3 * 270_692_000)
            verify_experiment(runner.records, runner.world)
            verify_domain(runner.records)
            runner.checkpoint(folder / "checkpoint.json")
            report = analyze(folder)
            self.assertTrue(report["technical_quality"]["compromised"])
            self.assertEqual(report["missing_responses_by_agent"], {"A": 1, "B": 1, "C": 1})
            self.assertEqual(len(report["missing_response_distribution"]), 3)
            self.assertEqual(len(Archive.read(folder)), len(runner.records))
            restored = Runner.restore(folder / "checkpoint.json")
            self.assertEqual(restored.state, runner.state)
            with self.assertRaises(PermissionError):
                await restored.step()
            target = Path(tmp) / "retrospective"
            prepared = prepare(folder, "decision-00000000", target, when="after")
            self.assertEqual(verify_origin(folder, target)["provider_calls"], 0)
            self.assertEqual(replay_recorded(folder)["provider_calls"], 0)
            self.assertEqual(Runner.restore(prepared).state["ledger"]["technical"]["total_unobserved"], 1)

    async def test_five_in_twenty_without_consecutive_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [missing(503) if i % 2 == 0 else completed() for i in range(9)]
            runner, transport = self.make(Path(tmp) / "research", cases, calls=9)
            for _ in cases:
                await runner.step()
            quality = runner.state["ledger"]["technical"]
            self.assertTrue(quality["compromised"])
            self.assertEqual(quality["total_unobserved"], 5)
            self.assertEqual(quality["trigger_reasons"], ["five_in_twenty"])
            self.assertEqual(quality["first_trigger_decision"], "decision-00000008")
            self.assertEqual(sum(quality["window_20"]), 5)
            self.assertEqual(transport.calls, 9)
            verify_experiment(runner.records, runner.world)

    async def test_ordinary_rejection_and_exhausted_domain_do_not_stop_driver(self):
        # A uses one domain slot, then attempts a second domain operation.
        scripts = {
            "A": [action("release", "not-owned", disposition="continue"),
                  action("release", "not-owned", disposition="continue"),
                  action("yield", disposition="yield_tick")],
            "B": [action("yield", disposition="yield_tick")] * 3,
            "C": [action("yield", disposition="yield_tick")] * 3}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "research"
            r = new_run(generate(0, 4), ScriptedMock(scripts), archive=folder,
                        clock=ManualClock(), cap=replace(limits(4), calls=5))
            report = await run_calibration(r)
            self.assertEqual(report["reason"], "configured_call_cap")
            self.assertEqual(r.state["ledger"]["calls"], 5)
            decisions = [x for x in r.records if x["event_type"] == "decision.completed"]
            self.assertEqual([x["agent"] for x in decisions[:4]], ["A", "A", "B", "C"])
            self.assertEqual(decisions[1]["computation"]["classification"], "environmental_rejection")
            self.assertTrue(decisions[1]["computation"]["proposal_well_formed"])
            self.assertEqual(decisions[1]["computation"]["selected_action"]["tool"], "act")
            self.assertEqual(decisions[1]["consequences"]["scheduling"]["basis"],
                             "platform_invalid_or_rejected_decision")
            self.assertEqual(decisions[2]["agent"], "B")
            self.assertTrue(report["technical_quality"]["total_unobserved"] == 0)
            self.assertEqual(decisions[4]["registration"]["observation"]["previous_decision_outcome"]["classification"],
                             "environmental_rejection")
            verify_experiment(r.records, r.world)
            verify_domain(r.records)

    async def test_http_429_html_and_503_non_json_remain_missed_without_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            responses = [(429, {'retry-after': '2'}, '<html>rate limited</html>'),
                         (503, {}, 'non-JSON upstream failure'), completed(), completed()]
            r, transport = self.make(Path(tmp) / 'research', responses, calls=4)
            first = await r.step()
            second = await r.step()
            self.assertEqual(first['computation']['classification'], 'unobserved_generation')
            self.assertEqual(second['computation']['classification'], 'unobserved_generation')
            self.assertEqual(first['generation']['provider_exchange']['response_text'], '<html>rate limited</html>')
            self.assertGreaterEqual(r.state['ledger']['elapsed_seconds'], 2)
            await r.step()
            await r.step()
            self.assertEqual(transport.calls, 4)
            verify_experiment(r.records, r.world)

    async def test_invalid_provider_usage_is_fatal_accounting_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = json.loads(completed())
            body['usage']['input_tokens_details']['cache_write_tokens'] = 999_999
            r, transport = self.make(Path(tmp) / 'research', [json.dumps(body)], calls=2)
            record = await r.step()
            self.assertEqual(record['computation']['classification'], 'fatal_integrity')
            self.assertEqual(r.state['status'], 'paused_provider')
            self.assertEqual(r.state['ledger']['unknown_reserved_tokens'], 1_050_000)
            self.assertEqual(transport.calls, 1)
            verify_experiment(r.records, r.world)

    async def test_invalid_body_missing_usage_and_fatal_unauthorized_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "research"
            r, t = self.make(folder, ["not json", completed(), completed(), completed()], calls=4)
            first = await r.step()
            self.assertEqual(first["computation"]["classification"], "invalid_or_absent_action")
            self.assertEqual(first["consequences"]["scheduling"]["basis"], "platform_invalid_or_rejected_decision")
            self.assertEqual(first["state_after"]["world"]["control"]["counts"]["A"]["operations"], 0)
            self.assertEqual([x["role"] for x in first["state_after"]["agents"]["A"]["history"][0]["messages"]], ["user"])
            for _ in range(3):
                await r.step()
            self.assertEqual(r.records[-1]["registration"]["observation"]["previous_decision_outcome"]["classification"],
                             "invalid_or_absent_action")
            verify_experiment(r.records, r.world)
        with tempfile.TemporaryDirectory() as tmp:
            r, t = self.make(Path(tmp) / "research", [missing(401)], calls=2)
            fatal = await r.step()
            self.assertEqual(fatal["computation"]["classification"], "fatal_integrity")
            self.assertEqual(r.state["status"], "paused_provider")
            self.assertEqual(r.state["stop_reason"], "provider_integrity_error")
            self.assertEqual(t.calls, 1)
            with self.assertRaises(RunLimitReached):
                await r.step()
            verify_experiment(r.records, r.world)

    async def test_provider_time_limits_and_missing_reservation_are_not_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = replace(limits(4), calls=4, total_tokens=1_100_000)
            r, t = self.make(Path(tmp) / "research", [missing(503), completed()], calls=4)
            r._state["config"]["limits"] = dict(r.state["config"]["limits"], total_tokens=cap.total_tokens)
            first = await r.step()
            self.assertEqual(first["computation"]["classification"], "unobserved_generation")
            with self.assertRaises(RunLimitReached):
                await r.step()
            self.assertEqual(t.calls, 1)
            self.assertEqual(r.state["ledger"]["unknown_reserved_tokens"], 1_050_000)
            verify_experiment(r.records, r.world)

    def test_retry_after_http_dates_and_invalid_values_are_conservative(self):
        self.assertEqual(retry_after_delay({"Retry-After": "4"}, 15), 4)
        self.assertEqual(retry_after_delay({"retry-after": "invalid"}, 15), 15)
        self.assertEqual(retry_after_delay({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}, 15), 0)


    async def test_protocol_invalid_matrix_has_single_ordinary_observation_feedback(self):
        # Every case is one authentic mock response plus other agents' turns;
        # no runner-side retry, instruction, or synthetic assistant/tool call.
        from consequence_lab.backends import ScriptedMock
        from consequence_lab.clock import ManualClock
        from consequence_lab.rooms_v3_run import new_run
        from consequence_lab.jsonio import clone
        from consequence_lab.audit import _decision
        good = action("release", "not-owned", disposition="yield_tick")
        invalids = {
            "bad_disposition": {"tool": "act", "arguments": {
                "resource": "/schedule", "arguments": {"operation": "release", "appointment_id": "not-owned"},
                "disposition": "invalid_disposition"}},
            "bad_disposition_rule": {"tool": "act", "arguments": {
                "resource": "/schedule", "arguments": {"operation": "release", "appointment_id": "not-owned"},
                "disposition": {"on_success": "continue", "on_rejection": "illegal"}}},
            "unsupported_tool": {"tool": "command", "arguments": {"path": "/schedule"}},
            "missing_required_room": {"tool": "act", "arguments": {
                "resource": "/schedule", "arguments": {"operation": "book", "appointment_id": "x",
                    "day": 1, "time": "09:00"}, "disposition": "yield_tick"}},
            "wrong_day_type": {"tool": "act", "arguments": {
                "resource": "/schedule", "arguments": {"operation": "book", "appointment_id": "x",
                    "day": "1", "time": "09:00", "room_id": "R0"}}},
            "bad_read_shape": {"tool": "read", "arguments": {"offset": 0, "disposition": "yield_tick"}},
            "bad_write_text": {"tool": "write", "arguments": {"path": "/home/notes",
                "text": 5, "disposition": "yield_tick"}},
            "bad_yield_disposition": {"tool": "act", "arguments": {
                "resource": "/opportunities", "arguments": {"operation": "yield"},
                "disposition": "continue"}},
            "bad_message_payload": {"tool": "act", "arguments": {
                "resource": "/messages", "arguments": {"operation": "send", "text": "Hi"}}},
            "unsupported_resource": {"tool": "act", "arguments": {
                "resource": "/unknown", "arguments": {"operation": "release", "appointment_id": "x"}}},
            "absent_proposal": None,
            "multiple_proposals": [good, good]
        }
        for name, item in invalids.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                actions = [] if item is None else item if isinstance(item, list) else [item]
                fixture = {"__response": {"text": "ACTUAL VISIBLE PROVIDER TEXT",
                    "actions": actions, "usage": {"input_tokens": 100, "output_tokens": 25, "source": "mock"}}}
                scripts = {
                    "A": [fixture, {"tool": "inspect", "arguments": {
                        "path": "/facility", "disposition": "yield_tick"}}],
                    "B": [action("yield", disposition="yield_tick")],
                    "C": [action("yield", disposition="yield_tick")]}
                directory = Path(temp) / "research"
                r = new_run(generate(0, 4), ScriptedMock(scripts), archive=directory,
                            clock=ManualClock(), cap=replace(limits(4), calls=4))
                first = await r.step()
                self.assertEqual(first["computation"]["classification"], "invalid_or_absent_action")
                self.assertFalse(first["computation"]["proposal_well_formed"])
                self.assertIsNone(first["computation"]["selected_action"])
                self.assertEqual(first["computation"]["proposed_actions"], actions)
                self.assertEqual(first["consequences"]["tool_response"]["status"], "rejected")
                receipt = first["consequences"]["scheduling"]
                self.assertFalse(receipt["operation_attempted"])
                self.assertEqual(receipt["budget_consumed"], "domain")
                self.assertEqual(receipt["applied_disposition"], "yield_tick")
                self.assertEqual(receipt["basis"], "platform_invalid_or_rejected_decision")
                c = first["state_after"]["world"]["control"]["counts"]["A"]
                self.assertEqual((c["generations"], c["operations"], c["domain_attempts"], c["domain_consumed"]),
                                 (1, 0, 0, 1))
                self.assertEqual(first["state_after"]["world"]["transactions"], [])
                self.assertEqual(first["state_after"]["artifacts"], {})
                history = first["state_after"]["agents"]["A"]["history"][0]["messages"]
                self.assertEqual([m["role"] for m in history], ["user", "assistant"])
                self.assertEqual(history[1]["content"], "ACTUAL VISIBLE PROVIDER TEXT")
                self.assertNotIn("tool_calls", history[1])
                self.assertTrue(all("action_error" not in m.get("content", "") for m in history))
                self.assertEqual(r.state["ledger"]["calls"], 1)
                for _ in range(3):
                    await r.step()
                next_a = [x for x in r.records if x["event_type"] == "decision.completed"][-1]
                self.assertEqual(next_a["agent"], "A")
                outcome = next_a["registration"]["observation"]["previous_decision_outcome"]
                self.assertEqual(outcome["classification"], "invalid_or_absent_action")
                self.assertEqual(outcome["decision_id"], first["decision_id"])
                self.assertEqual(outcome["tool_result"], first["consequences"]["tool_response"])
                request = next_a["generation"]["request"]["messages"]
                self.assertEqual(sum("ACTUAL VISIBLE PROVIDER TEXT" in m.get("content","") for m in request), 1)
                self.assertEqual(sum("previous_decision_outcome" in m.get("content","") for m in request), 1)
                self.assertEqual(sum("action_error" in m.get("content","") for m in request), 0)
                self.assertEqual(sum(m["role"] == "user" for m in request), 3)
                self.assertEqual([m["role"] for m in next_a["state_before"]["agents"]["A"]["history"][0]["messages"]],
                                 ["user", "assistant"])
                verify_experiment(r.records, r.world)
                verify_domain(r.records)
                # Check the independent reconstruction catches both a classification
                # tamper and a fabricated history feedback role.
                altered = clone(first)
                altered["computation"]["classification"] = "environmental_rejection"
                with self.assertRaises(ValueError):
                    _decision(altered, r.world)
                altered = clone(first)
                altered["state_after"]["agents"]["A"]["history"][0]["messages"].append(
                    {"role": "user", "content": '{"action_error":"fabricated"}'})
                with self.assertRaises(ValueError):
                    _decision(altered, r.world)
                r.checkpoint(directory / "checkpoint.json")
                report = analyze(directory)
                self.assertEqual(report["decisions"][0]["classification"], "invalid_or_absent_action")
                self.assertEqual(len(Archive.read(directory)), len(r.records))
                self.assertEqual(Runner.restore(directory / "checkpoint.json").state, r.state)
                if name == "bad_disposition":
                    branch_path = Path(temp) / "branch"
                    prepared = prepare(directory, first["decision_id"], branch_path, when="after")
                    self.assertEqual(Runner.restore(prepared).state, first["state_after"])
                    self.assertEqual(verify_origin(directory, branch_path)["provider_calls"], 0)
                    self.assertEqual(replay_recorded(directory)["provider_calls"], 0)

    async def test_identifiable_invalid_schedule_values_are_environmental_attempts(self):
        from consequence_lab.jsonio import clone
        baseline = action("book", "A-01-1", day=1, time="09:00", room_id="R0")
        for field, value in (("day", 0), ("day", 61), ("time", "11:00"),
                             ("room_id", "unknown-room"), ("appointment_id", "")):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                proposed = clone(baseline)
                proposed["arguments"]["arguments"][field] = value
                scripts = {"A": [proposed, action("yield")],
                           "B": [action("yield")], "C": [action("yield")]}
                folder = Path(tmp) / "research"
                r = new_run(generate(0, 4), ScriptedMock(scripts), archive=folder,
                            clock=ManualClock(), cap=replace(limits(4), calls=4))
                records = [await r.step() for _ in range(4)]
                first, next_a = records[0], records[3]
                self.assertEqual(first["computation"]["classification"], "environmental_rejection")
                self.assertTrue(first["computation"]["proposal_well_formed"])
                self.assertIsNotNone(first["computation"]["selected_action"])
                self.assertEqual(first["consequences"]["tool_response"]["status"], "rejected")
                self.assertTrue(first["consequences"]["scheduling"]["operation_attempted"])
                counts = first["state_after"]["world"]["control"]["counts"]["A"]
                self.assertEqual((counts["operations"], counts["domain_attempts"], counts["domain_consumed"]), (1, 1, 1))
                self.assertEqual(first["state_after"]["world"]["transactions"], [])
                messages = first["state_after"]["agents"]["A"]["history"][-1]["messages"]
                self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool"])
                self.assertEqual(next_a["agent"], "A")
                outcome = next_a["registration"]["observation"]["previous_decision_outcome"]
                self.assertEqual(outcome["classification"], "environmental_rejection")
                self.assertEqual(outcome["tool_result"], first["consequences"]["tool_response"])
                self.assertEqual(r.state["status"], "running")
                verify_experiment(r.records, r.world)
                verify_domain(r.records)
                r.checkpoint(folder / "checkpoint.json")
                self.assertEqual(Runner.restore(folder / "checkpoint.json").state, r.state)

    async def test_structurally_valid_environmental_rejections_preserve_operation_attempts(self):
        # Not-owned and then exhausted domain are environmental, not protocol failures.
        from consequence_lab.backends import ScriptedMock
        from consequence_lab.clock import ManualClock
        from consequence_lab.rooms_v3_run import new_run
        scripts = {"A": [
            action("release", "not-owned", disposition="continue"),
            action("release", "not-owned", disposition="continue"),
            action("yield", disposition="yield_tick")],
            "B": [action("yield", disposition="yield_tick")],
            "C": [action("yield", disposition="yield_tick")]}
        with tempfile.TemporaryDirectory() as tmp:
            r = new_run(generate(0, 4), ScriptedMock(scripts), clock=ManualClock(),
                        archive=Path(tmp) / "research", cap=replace(limits(4), calls=5))
            one = await r.step()
            two = await r.step()
            for record in (one, two):
                self.assertEqual(record["computation"]["classification"], "environmental_rejection")
                self.assertTrue(record["computation"]["proposal_well_formed"])
                self.assertTrue(record["consequences"]["scheduling"]["operation_attempted"])
                self.assertEqual(record["consequences"]["tool_response"]["status"], "rejected")
                self.assertEqual(record["computation"]["selected_action"]["tool"], "act")
                messages = record["state_after"]["agents"]["A"]["history"][-1]["messages"]
                self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool"])
                self.assertNotIn("action_error", str(messages))
            self.assertEqual(two["consequences"]["tool_response"]["error"], "Domain opportunity exhausted")
            counts = r.state["world"]["control"]["counts"]["A"]
            self.assertEqual((counts["generations"], counts["operations"], counts["domain_attempts"]),
                             (2, 2, 2))
            self.assertEqual((counts["domain_consumed"], counts["information_consumed"]), (1, 1))
            self.assertEqual(two["consequences"]["scheduling"]["applied_disposition"], "yield_tick")
            self.assertEqual(two["consequences"]["scheduling"]["basis"], "platform_invalid_or_rejected_decision")
            await r.step()
            await r.step()
            resumed = await r.step()
            self.assertEqual(resumed["agent"], "A")
            self.assertEqual(resumed["registration"]["observation"]["previous_decision_outcome"]["classification"],
                             "environmental_rejection")
            verify_experiment(r.records, r.world)
            verify_domain(r.records)
            path = Path(tmp) / "research"
            r.checkpoint(path / "checkpoint.json")
            self.assertEqual(Runner.restore(path / "checkpoint.json").state, r.state)


if __name__ == "__main__":
    unittest.main()
