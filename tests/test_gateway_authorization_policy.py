import unittest

from src.audit.audit_logger import AuditLogger
from src.core.data_models import AgentCard, ProtocolType, Task, TaskType
from src.gateway.gateway import Gateway
from src.registry.registry import Registry


class GatewayAuthorizationPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.local = Registry("finance-local")
        self.global_reg = Registry("global", is_global=True)
        self.gateway = Gateway(
            gateway_id="finance-gw",
            sub_ioa_id="finance",
            local_registry=self.local,
            global_registry=self.global_reg,
            audit_logger=AuditLogger("global"),
        )

    async def _register_requester(self, scopes):
        card = AgentCard(
            agent_id="requester-1",
            display_name="Requester",
            provider="test",
            sub_ioa_id="finance",
            declared_capabilities=["request"],
            supported_protocols=[ProtocolType.A2A],
            certificate=None,
            permission_scope=scopes,
        )
        await self.local.register(card)
        await self.global_reg.register(card)

    async def test_authorization_denies_missing_data_domain_scope(self):
        await self._register_requester(["execute", "read_financial"])
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Analyze patient-related investment risk",
            payload={"data_domains": ["patient"]},
        )

        result = await self.gateway._check_authorization("requester-1", task)

        self.assertFalse(result.authorized)
        self.assertIn("read_patient", result.reason)

    async def test_authorization_allows_required_data_domain_scope(self):
        await self._register_requester(["execute", "read_patient"])
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Analyze patient-related investment risk",
            payload={"data_domains": ["patient"]},
        )

        result = await self.gateway._check_authorization("requester-1", task)

        self.assertTrue(result.authorized)

    async def test_abac_denies_requester_below_min_reputation(self):
        await self._register_requester(["execute", "read"])
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Analyze a high trust transaction",
            payload={"min_reputation": 0.8},
        )

        result = await self.gateway._check_authorization("requester-1", task)

        self.assertFalse(result.authorized)
        self.assertIn("min_reputation", result.reason)

    async def test_abac_denies_disallowed_sub_ioa(self):
        await self._register_requester(["execute", "read"])
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Analyze restricted data",
            payload={"allowed_requester_sub_ioas": ["healthcare"]},
        )

        result = await self.gateway._check_authorization("requester-1", task)

        self.assertFalse(result.authorized)
        self.assertIn("allowed_requester_sub_ioas", result.reason)

    async def test_human_approval_required_is_fail_closed(self):
        await self._register_requester(["execute"])
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Execute high-impact decision",
            payload={"human_approval_required": True},
        )

        result = await self.gateway._check_authorization("requester-1", task)

        self.assertFalse(result.authorized)
        self.assertIn("Human approval required", result.reason)


if __name__ == "__main__":
    unittest.main()
