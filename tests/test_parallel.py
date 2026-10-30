"""Seed resolution runs on a bounded thread pool.

These tests assert behaviour that matters: results stay in member/seed order,
tags and entities are both handled, and the pool is actually used.
"""

import threading
import time
import unittest
from typing import Any, Mapping

from sociosense.agent import SocioSenseAgent
from sociosense.clients.facts import NullFactsProvider
from sociosense.clients.qloo import QlooClient
from sociosense.config import Settings
from tests.test_agent import members


class SlowStubTransport:
    """Answers search/tags after a short sleep and records the worker thread."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.threads: list[int] = []
        self._lock = threading.Lock()

    def get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        with self._lock:
            self.threads.append(threading.get_ident())
        time.sleep(self.delay)

        if url.endswith("/v2/tags"):
            query = params["filter.query"]
            return {
                "success": True,
                "results": {
                    "tags": [
                        {
                            "id": f"tag-{query}",
                            "name": query.title(),
                            "type": "urn:tag:genre:place",
                            "parents": [{"type": "urn:entity:place"}],
                            "popularity": 0.9,
                        }
                    ]
                },
            }
        if url.endswith("/v2/search"):
            query = params["query"]
            types = params.get("types") or ["urn:entity:movie"]
            return {
                "success": True,
                "results": [
                    {
                        "entity_id": f"id-{query.lower()}",
                        "name": query,
                        "subtype": types[0] if isinstance(types, list) else types,
                        "popularity": 0.9,
                    }
                ],
            }
        return {"success": True, "results": {"entities": []}}


def make_agent(transport: SlowStubTransport, workers: int) -> SocioSenseAgent:
    client = QlooClient("key", transport=transport, cache_ttl_s=0)
    return SocioSenseAgent(client, NullFactsProvider(), Settings(resolve_workers=workers), data_mode="fixture")


class ParallelResolveTests(unittest.TestCase):
    def test_resolves_every_seed_and_preserves_order(self):
        transport = SlowStubTransport()
        trace: list = []

        resolved, issues = make_agent(transport, workers=4)._resolve(members(), trace)

        self.assertEqual(issues, [])
        for original, after in zip(members(), resolved):
            self.assertEqual([s.query for s in original.seeds], [s.query for s in after.seeds])
            self.assertTrue(all(s.resolved_id for s in after.seeds))
        self.assertEqual(trace[0].detail, "resolved 8/8 taste seeds")

    def test_pool_is_actually_used(self):
        transport = SlowStubTransport()
        trace: list = []

        make_agent(transport, workers=4)._resolve(members(), trace)

        self.assertGreater(len(set(transport.threads)), 1)
        self.assertEqual(trace[0].payload["workers"], 4)

    def test_single_worker_falls_back_to_sequential(self):
        transport = SlowStubTransport(delay=0.0)
        trace: list = []

        resolved, issues = make_agent(transport, workers=1)._resolve(members(), trace)

        self.assertEqual(issues, [])
        self.assertEqual(len(set(transport.threads)), 1)
        self.assertEqual(trace[0].payload["workers"], 1)

    def test_failed_lookup_does_not_cancel_other_seeds(self):
        class FlakyTransport(SlowStubTransport):
            def get_json(self, url, params=None, headers=None):
                if (params or {}).get("query") == "The Bear":
                    raise RuntimeError("boom")
                return super().get_json(url, params, headers)

        transport = FlakyTransport(delay=0.0)
        resolved, issues = make_agent(transport, workers=4)._resolve(members(), [])

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].seed.query, "The Bear")
        resolved_ids = [s.resolved_id for m in resolved for s in m.seeds if s.resolved_id]
        self.assertEqual(len(resolved_ids), 7)


if __name__ == "__main__":
    unittest.main()
