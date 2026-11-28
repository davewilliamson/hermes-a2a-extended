import asyncio

from async_fixture import running, terminal


def test_real_gateway_native_dispatch(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_ingress_context import register_transport_home
    from gateway.session_results import execution_result

    async def test():
        async with running(tmp_path, monkeypatch) as (instance, rpc, *_):
            runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / "sessions"))
            runner.adapters = {instance.platform: instance}
            instance.gateway_runner = runner
            runner._gateway_loop = asyncio.get_running_loop()
            runner._running = True
            authority = await initialize_session_authority(runner, profile_id="default", instance_id="a2a-async-fixture")
            register_transport_home(runner, None, instance.home)
            runner._wire_adapter_handlers(instance)
            turns = []

            async def stub_agent(event, source, quick_key, run_generation):
                turns.append((event.text, event.allow_gateway_control, event.get_command(), source.platform))
                captured = execution_result.get()
                if captured is not None:
                    captured["result"] = {"final_response": "native gateway reply", "messages": []}
                return "native gateway reply"

            monkeypatch.setattr(runner, "_handle_message_with_agent", stub_agent)
            try:
                ack = await rpc("tasks/send", messageId="native", message="/approve once")
                result = await terminal(rpc, ack)
                assert result["state"] == "COMPLETED" and result["reply"] == "native gateway reply"
                assert turns == [("[Authenticated A2A task]\n/approve once", False, None, instance.platform)]
                admissions = [admission for session_id in authority.sessions
                              for admission in authority.persistence.admissions.list(
                                  session_id=session_id, pending_only=False)]
                assert len(admissions) == 1
            finally:
                runner._running = False
    asyncio.run(test())
