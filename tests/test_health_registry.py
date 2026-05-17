"""
Unit tests for Common.health.

The registry is a tiny piece but it's on the operational hot path
(/health endpoint, alerting), so we pin down its behavior precisely:
  - aggregation picks the worst status
  - components missed for >stale_after_seconds count as DEGRADED
  - register() makes a component visible before its first report
  - snapshot shape stays stable for downstream JSON consumers
"""

import os
import sys
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Common.health import HealthRegistry, HealthStatus  # noqa: E402


class HealthStatusOrderingTest(unittest.TestCase):
    def test_worst_picks_unhealthy(self):
        self.assertEqual(
            HealthStatus.worst([
                HealthStatus.HEALTHY,
                HealthStatus.DEGRADED,
                HealthStatus.UNHEALTHY,
            ]),
            HealthStatus.UNHEALTHY,
        )

    def test_worst_picks_degraded_over_unknown(self):
        # Unknown > healthy but < degraded — a never-reported component
        # shouldn't mask a known-degraded one.
        self.assertEqual(
            HealthStatus.worst([HealthStatus.UNKNOWN, HealthStatus.DEGRADED]),
            HealthStatus.DEGRADED,
        )

    def test_worst_returns_healthy_when_all_healthy(self):
        self.assertEqual(
            HealthStatus.worst([HealthStatus.HEALTHY, HealthStatus.HEALTHY]),
            HealthStatus.HEALTHY,
        )


class HealthRegistryTest(unittest.TestCase):
    def test_empty_registry_is_unknown(self):
        reg = HealthRegistry()
        self.assertEqual(reg.overall(), HealthStatus.UNKNOWN)
        self.assertEqual(reg.snapshot()["components"], [])

    def test_register_makes_component_visible(self):
        reg = HealthRegistry()
        reg.register("datafeed")
        snap = reg.snapshot()
        self.assertEqual(len(snap["components"]), 1)
        self.assertEqual(snap["components"][0]["name"], "datafeed")
        self.assertEqual(snap["components"][0]["status"], "unknown")

    def test_report_updates_status(self):
        reg = HealthRegistry()
        reg.report("llm", HealthStatus.HEALTHY, detail="all good")
        c = reg.get("llm")
        self.assertIsNotNone(c)
        self.assertEqual(c.status, HealthStatus.HEALTHY)
        self.assertEqual(c.detail, "all good")

    def test_overall_aggregates_worst(self):
        reg = HealthRegistry()
        reg.report("a", HealthStatus.HEALTHY)
        reg.report("b", HealthStatus.DEGRADED)
        reg.report("c", HealthStatus.HEALTHY)
        self.assertEqual(reg.overall(), HealthStatus.DEGRADED)

        reg.report("c", HealthStatus.UNHEALTHY)
        self.assertEqual(reg.overall(), HealthStatus.UNHEALTHY)

    def test_stale_components_downgrade_to_degraded(self):
        reg = HealthRegistry(stale_after_seconds=0.05)
        reg.report("flaky", HealthStatus.HEALTHY)
        # Wait past the staleness window.
        time.sleep(0.06)
        # Component still says healthy, but registry treats it as
        # degraded because it hasn't been refreshed.
        self.assertEqual(reg.overall(), HealthStatus.DEGRADED)
        snap = reg.snapshot()
        # The stale flag is exposed for monitors.
        flaky_entry = next(c for c in snap["components"] if c["name"] == "flaky")
        self.assertTrue(flaky_entry.get("stale"))

    def test_extra_fields_round_trip(self):
        reg = HealthRegistry()
        reg.report(
            "datafeed",
            HealthStatus.DEGRADED,
            detail="proxy",
            stale_age_seconds=42.0,
        )
        snap = reg.snapshot()
        df = next(c for c in snap["components"] if c["name"] == "datafeed")
        self.assertEqual(df["status"], "degraded")
        self.assertEqual(df["stale_age_seconds"], 42.0)


if __name__ == "__main__":
    unittest.main()
