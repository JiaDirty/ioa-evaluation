import unittest

from src.core.data_models import (
    ActorType,
    AgentCard,
    Artifact,
    AuditAction,
    AuditEvent,
    CapabilityClaim,
    EndpointDescriptor,
    GatewayPipelineStage,
    PolicyTicket,
    ProtocolSupport,
    ProtocolType,
    TaskConstraints,
    TaskEnvelope,
    TaskType,
)


class DesignContractModelsTest(unittest.TestCase):
    def test_design_doc_contract_models_are_instantiable(self):
        task = TaskEnvelope(
            task_type=TaskType.SINGLE_DOMAIN,
            description="contract task",
            target_sub_ioas=["finance"],
            user_goal="assess risk",
            constraints=TaskConstraints(
                allowed_protocols=[ProtocolType.A2A],
                human_approval_required=True,
            ),
            user_grants=["execute"],
            trace_id="trace-1",
        )
        self.assertEqual(task.trace_id, "trace-1")
        self.assertTrue(task.constraints.human_approval_required)

        card = AgentCard(
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            capability_claims=[
                CapabilityClaim(
                    capability_id="financial_analysis",
                    declared_by="finance-org",
                )
            ],
            supported_protocols=[ProtocolType.A2A],
            protocol_support=[ProtocolSupport(protocol=ProtocolType.A2A)],
            endpoint="http://127.0.0.1/agents/finance",
            endpoint_descriptor=EndpointDescriptor(
                url="http://127.0.0.1/agents/finance",
                protocol=ProtocolType.A2A,
            ),
        )
        self.assertEqual(card.endpoint_descriptor.protocol, ProtocolType.A2A)
        self.assertEqual(card.capability_claims[0].capability_id, "financial_analysis")

        ticket = PolicyTicket(
            task_id=task.task_id,
            allowed=False,
            reason="Human approval required",
            denied_scopes=["execute"],
            human_approval_checked=True,
        )
        self.assertFalse(ticket.allowed)
        self.assertTrue(ticket.human_approval_checked)

        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id=card.agent_id,
            protocol=ProtocolType.A2A.value,
            content="result",
            source_agent_id=card.agent_id,
            source_task_id=task.task_id,
            safety_labels=["reviewed"],
        )
        self.assertEqual(artifact.task_id, task.task_id)
        self.assertEqual(artifact.producer_agent_id, card.agent_id)

        event = AuditEvent(
            trace_id=task.trace_id,
            step_index=0,
            action=AuditAction.AUTH_CHECK,
            agent_id="finance-gw",
            sub_ioa_id="finance",
            actor_type=ActorType.POLICY_ENGINE,
            actor_id="policy",
            details={"stage": GatewayPipelineStage.POLICY_ENFORCEMENT.value},
        )
        self.assertEqual(event.event_type, AuditAction.AUTH_CHECK.value)
        self.assertEqual(event.task_id, task.trace_id)


if __name__ == "__main__":
    unittest.main()
