"""Dependency injection container for Jarvis OS.

Why this exists: Feature routers and runtime services receive dependencies via
AppContainer rather than importing app.main, eliminating circular imports and making
every service trivially testable against temporary directories.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.brain.agents import AgentStore
from app.brain.character import CharacterManager
from app.brain.core_memory import CoreMemoryManager
from app.brain.hybrid_search import HybridIndexStore
from app.brain.prompt_builder import PromptBuilder
from app.brain.skills import SkillStore
from app.brain.vault import VaultManager
from app.brain.watcher import VaultWatcherService
from app.channels.base import TurnShell
from app.channels.telegram import TelegramBotService
from app.config import AppPaths, DEFAULT_BRAIN_NAME, SettingsStore, is_public_bind, parse_allowed_hosts
from app.engines.base import EngineProtocol
from app.engines.compaction import SessionCompactor
from app.engines.router import EngineRouter
from app.hub.approval import ApprovalManager
from app.hub.gateway import MCPHubGateway
from app.hub.plugins import PluginHost
from app.runtime.chat_runtime import ChatRuntime
from app.runtime.experience import ExperienceStore
from app.runtime.inbox import InboxStore
from app.runtime.kanban import KanbanRuntime
from app.runtime.learn import SelfLearningEngine
from app.runtime.loops import LoopRunner
from app.runtime.reminders import ReminderStore
from app.runtime.sessions import SessionStore
from app.runtime.telemetry import TraceStore
from app.runtime.usage import UsageStore
from app.runtime.webhooks import WebhookStore
from app.runtime.workflows import WorkflowRunner
from app.security.auth import AuthManager
from app.security.secrets import SecretStore
from app.voice.voice_brain import VoiceBrain


@dataclass
class AppContainer:
    paths: AppPaths
    host: str
    port: int
    allowed_hosts: set[str]
    secrets: SecretStore
    settings: SettingsStore
    auth: AuthManager
    vaults: VaultManager
    sessions: SessionStore
    skills: SkillStore
    agents: AgentStore
    plugins: PluginHost
    hub: MCPHubGateway
    prompt_builder: PromptBuilder
    engine_router: EngineRouter
    inbox: InboxStore
    usage: UsageStore
    telemetry: TraceStore
    approvals: ApprovalManager
    compactor: SessionCompactor
    learner: SelfLearningEngine
    experience: ExperienceStore
    core_memory: CoreMemoryManager
    characters: CharacterManager
    hybrid_index: HybridIndexStore
    workflows: WorkflowRunner
    kanban: KanbanRuntime
    loops: LoopRunner
    reminders: ReminderStore
    chat_runtime: ChatRuntime
    turn_shell: TurnShell
    telegram: TelegramBotService
    voice_brain: VoiceBrain
    webhooks: WebhookStore
    watcher: VaultWatcherService

    @classmethod
    def build(
        cls,
        *,
        code_dir: Path | None = None,
        state_dir: Path | None = None,
        brains_dir: Path | None = None,
        host: str | None = None,
        port: int | None = None,
        force_login: bool | None = None,
        engine_override: EngineProtocol | None = None,
    ) -> "AppContainer":
        base_paths = AppPaths.from_env(code_dir=code_dir)
        paths = AppPaths(
            code_dir=base_paths.code_dir,
            state_dir=(state_dir or base_paths.state_dir).resolve(),
            brains_dir=(brains_dir or base_paths.brains_dir).resolve(),
            prompts_dir=base_paths.prompts_dir,
            dashboard_dist_dir=base_paths.dashboard_dist_dir,
        )
        paths.ensure_dirs()

        bind_host = host or os.getenv("JARVIS_HOST", "127.0.0.1")
        bind_port = port or int(os.getenv("JARVIS_PORT", "7777"))
        if force_login is None:
            env_req = (os.getenv("JARVIS_REQUIRE_LOGIN") or "").strip().lower()
            if env_req in {"1", "true", "yes"}:
                require_login = True
            elif env_req in {"0", "false", "no"}:
                require_login = False
            else:
                require_login = is_public_bind(bind_host)
        else:
            require_login = force_login

        secret_store = SecretStore(paths.state_dir)
        settings_store = SettingsStore(paths.state_dir, secret_store)
        auth_manager = AuthManager(paths.state_dir, force_login=require_login)
        vault_manager = VaultManager(paths.brains_dir)
        session_store = SessionStore(paths.state_dir / "conversations.db")

        # Always guarantee the active brain (default: Brain Default) exists with its scaffold.
        active_brain = str(settings_store.load().get("active_brain") or DEFAULT_BRAIN_NAME)
        vault_manager.ensure_brain(active_brain)

        skill_store = SkillStore(paths.code_dir, vault_manager)
        skill_store.sync_system_skills(active_brain)
        agent_store = AgentStore(vault_manager)

        hub = MCPHubGateway(
            code_dir=paths.code_dir,
            state_dir=paths.state_dir,
            port=bind_port,
            vaults=vault_manager,
            secrets_store=secret_store,
        )
        plugin_services: dict = {
            "settings": settings_store,
            "skills": skill_store,
            "agents": agent_store,
            "vaults": vault_manager,
            "hub": hub,
        }
        plugin_host = PluginHost(
            code_dir=paths.code_dir,
            state_dir=paths.state_dir,
            vaults=vault_manager,
            services=plugin_services,
        )
        hub.register_tool_provider(plugin_host.get_tools)
        hub.set_hook_runner(plugin_host.fire_hooks)
        hub.set_skills_store(skill_store)

        core_memory = CoreMemoryManager(vault_manager)
        plugin_services["core_memory"] = core_memory
        hub.set_core_memory(core_memory)
        characters = CharacterManager(vault_manager)
        plugin_services["characters"] = characters
        hub.set_character_manager(characters)
        hybrid_index = HybridIndexStore(paths.state_dir / "hybrid_index.sqlite3")
        plugin_services["hybrid_index"] = hybrid_index
        hub.set_hybrid_index(hybrid_index)

        prompt_builder = PromptBuilder(
            paths.prompts_dir,
            vault_manager,
            skills=skill_store,
            core_memory=core_memory,
            characters=characters,
        )
        engine_router = EngineRouter(settings_store, vaults=vault_manager, hub=hub)
        hub.set_engine_router(engine_router)
        experience = ExperienceStore(paths.state_dir)
        plugin_services["experience"] = experience
        hub.set_experience_store(experience)
        inbox = InboxStore(paths.state_dir, session_store)
        usage = UsageStore(paths.state_dir)
        telemetry = TraceStore(paths.state_dir)
        hub.set_telemetry(telemetry)
        engine_router.set_telemetry(telemetry)

        approval_cfg = settings_store.load().get("approval", {})
        approvals = ApprovalManager(
            default_timeout_s=float(approval_cfg.get("timeout_s", 120.0))
        )
        hub.set_approval_manager(approvals, settings_store)
        plugin_services["approvals"] = approvals
        compactor = SessionCompactor(
            state_dir=paths.state_dir,
            sessions=session_store,
            router=engine_router,
            engine_override=engine_override,
        )
        learner = SelfLearningEngine(
            state_dir=paths.state_dir,
            vaults=vault_manager,
            sessions=session_store,
            router=engine_router,
            engine_override=engine_override,
        )
        workflow_runner = WorkflowRunner(
            state_dir=paths.state_dir,
            agents=agent_store,
            router=engine_router,
            hub=hub,
            engine_override=engine_override,
        )
        kanban = KanbanRuntime(
            state_dir=paths.state_dir,
            router=engine_router,
            hub=hub,
            workflows=workflow_runner,
            inbox=inbox,
            engine_override=engine_override,
        )
        loops = LoopRunner(
            vaults=vault_manager,
            router=engine_router,
            hub=hub,
            inbox=inbox,
            engine_override=engine_override,
        )
        reminders = ReminderStore(
            vaults=vault_manager,
            router=engine_router,
            hub=hub,
            inbox=inbox,
            engine_override=engine_override,
        )
        plugin_services["kanban"] = kanban
        plugin_services["loops"] = loops
        plugin_services["reminders"] = reminders
        plugin_services["inbox"] = inbox

        chat_runtime = ChatRuntime(
            sessions=session_store,
            router=engine_router,
            prompt_builder=prompt_builder,
            hub=hub,
            compactor=compactor,
            usage=usage,
            telemetry=telemetry,
            engine_override=engine_override,
        )
        inbox.set_ws_publisher(chat_runtime.publish)
        approvals.add_notifier(chat_runtime.publish)

        turn_shell = TurnShell(
            settings=settings_store,
            sessions=session_store,
            router=engine_router,
            prompt_builder=prompt_builder,
            hub=hub,
            compactor=compactor,
            usage=usage,
            engine_override=engine_override,
        )
        telegram = TelegramBotService(settings=settings_store, turn_shell=turn_shell)
        inbox.add_channel_notifier(telegram.notify_from_inbox)

        voice_brain = VoiceBrain(
            prompts_dir=paths.prompts_dir,
            router=engine_router,
            sessions=session_store,
            chat_runtime=chat_runtime,
            engine_override=engine_override,
        )
        webhooks = WebhookStore(paths.state_dir)
        watcher = VaultWatcherService(
            vaults=vault_manager,
            hybrid_index=hybrid_index,
            brains_dir=paths.brains_dir,
        )

        return cls(
            paths=paths,
            host=bind_host,
            port=bind_port,
            allowed_hosts=parse_allowed_hosts(bind_host, bind_port),
            secrets=secret_store,
            settings=settings_store,
            auth=auth_manager,
            vaults=vault_manager,
            sessions=session_store,
            skills=skill_store,
            agents=agent_store,
            plugins=plugin_host,
            hub=hub,
            prompt_builder=prompt_builder,
            engine_router=engine_router,
            inbox=inbox,
            usage=usage,
            telemetry=telemetry,
            approvals=approvals,
            compactor=compactor,
            learner=learner,
            experience=experience,
            core_memory=core_memory,
            characters=characters,
            hybrid_index=hybrid_index,
            workflows=workflow_runner,
            kanban=kanban,
            loops=loops,
            reminders=reminders,
            chat_runtime=chat_runtime,
            turn_shell=turn_shell,
            telegram=telegram,
            voice_brain=voice_brain,
            webhooks=webhooks,
            watcher=watcher,
        )
