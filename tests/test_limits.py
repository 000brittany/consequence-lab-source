import unittest

from consequence_lab.backends import ScriptedMock
from consequence_lab.clock import ManualClock
from consequence_lab.config import Limits
from consequence_lab.runner import Runner, RunLimitReached


class LimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_cap_survives_checkpoint(self):
        runner = Runner.validation(clock=ManualClock(), limits=Limits(calls=1))
        await runner.step()
        restored = Runner.restore(runner.checkpoint())
        with self.assertRaises(RunLimitReached):
            await restored.step()
        self.assertEqual(restored.state["ledger"]["calls"], 1)
        self.assertEqual(restored.state["stop_reason"], "calls")

    async def test_total_token_reservation_blocks_before_call(self):
        runner = Runner.validation(clock=ManualClock(), limits=Limits(total_tokens=10))
        with self.assertRaises(RunLimitReached):
            await runner.step()
        self.assertEqual(runner.state["ledger"]["calls"], 0)
        self.assertEqual(runner.state["stop_reason"], "total_tokens")

    async def test_spend_limit_and_pricing_restore(self):
        runner = Runner.validation(clock=ManualClock(), backend=ScriptedMock(input_rate_nano_usd=1),
                                   limits=Limits(spend_nano_usd=1))
        restored = Runner.restore(runner.checkpoint())
        self.assertEqual(restored.backend.input_rate_nano_usd, 1)
        with self.assertRaises(RunLimitReached):
            await restored.step()
        self.assertEqual(restored.state["ledger"]["calls"], 0)
        self.assertEqual(restored.state["stop_reason"], "spend")

    async def test_output_allowance_causes_no_partial_action(self):
        runner = Runner.validation(clock=ManualClock(), limits=Limits(output_tokens=1))
        record = await runner.step()
        self.assertEqual(record["computation"]["response"]["finish_reason"], "length")
        self.assertEqual(record["state_before"]["world"], record["state_after"]["world"])
        self.assertEqual(record["accounting"]["usage"]["source"], "mock")
        self.assertLessEqual(record["accounting"]["usage"]["output_tokens"], 1)

    async def test_elapsed_time_and_pacing(self):
        clock = ManualClock()
        runner = Runner.validation(clock=clock, limits=Limits(min_interval_seconds=2, elapsed_seconds=3))
        await runner.run(3)
        self.assertEqual(runner.state["ledger"]["calls"], 2)
        self.assertEqual(runner.state["stop_reason"], "pacing_would_exceed_time")
        self.assertEqual(clock.elapsed(), 2)

    async def test_rate_limit_has_no_hidden_retry_and_pacing_restores(self):
        scripts = {"agent_0": [{"__rate_limit_seconds": 3}]}
        runner = Runner.validation(clock=ManualClock(), backend=ScriptedMock(scripts))
        record = await runner.step()
        self.assertEqual(record["computation"]["error"]["type"], "RateLimited")
        self.assertEqual(runner.state["ledger"]["calls"], 1)
        self.assertGreater(runner.state["ledger"]["unknown_reserved_tokens"], 0)
        restored = Runner.restore(runner.checkpoint())
        left, right = await runner.step(), await restored.step()
        self.assertEqual(left, right)
        self.assertEqual(restored.clock.elapsed(), 3)
        self.assertEqual(restored.state["ledger"]["calls"], 2)

    async def test_timeout_retains_unknown_usage_and_applies_no_action(self):
        scripts = {"agent_0": [{"__delay_seconds": .1}]}
        runner = Runner.validation(backend=ScriptedMock(scripts), limits=Limits(elapsed_seconds=.02))
        record = await runner.step()
        self.assertIsNotNone(record["computation"]["error"])
        self.assertEqual(record["state_before"]["world"], record["state_after"]["world"])
        self.assertIsNone(record["accounting"]["usage"])
        self.assertGreater(runner.state["ledger"]["unknown_reserved_tokens"], 0)

    async def test_reported_usage_violation_stops_before_world_effect(self):
        scripts = {"agent_0": [{"__response": {"actions": [{"tool": "act", "arguments": {
            "resource": "/calendar", "arguments": {"operation": "book", "request_id": "agent_0:r1", "slot": 1}}}],
            "usage": {"input_tokens": 1, "output_tokens": 513, "source": "mock"}}}]}
        runner = Runner.validation(clock=ManualClock(), backend=ScriptedMock(scripts))
        record = await runner.step()
        self.assertEqual(record["state_before"]["world"], record["state_after"]["world"])
        self.assertEqual(runner.state["stop_reason"], "backend_usage_violation")

    async def test_real_backend_is_not_enabled(self):
        class Disallowed(ScriptedMock):
            name = "api_backend"
        with self.assertRaises(ValueError):
            Runner.validation(backend=Disallowed())
