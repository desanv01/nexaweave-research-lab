"""Inherited service exports without eager SDK, runner, or parser imports."""

from importlib import import_module

__all__ = [
    'OntologyGenerator', 'GraphBuilderService', 'TextProcessor',
    'ZepEntityReader', 'EntityNode', 'FilteredEntities',
    'OasisProfileGenerator', 'OasisAgentProfile',
    'SimulationManager', 'SimulationState', 'SimulationStatus',
    'SimulationConfigGenerator', 'SimulationParameters', 'AgentActivityConfig',
    'TimeSimulationConfig', 'EventConfig', 'PlatformConfig',
    'SimulationRunner', 'SimulationRunState', 'RunnerStatus', 'AgentAction', 'RoundSummary',
    'ZepGraphMemoryUpdater', 'ZepGraphMemoryManager', 'AgentActivity',
    'SimulationIPCClient', 'SimulationIPCServer', 'IPCCommand', 'IPCResponse',
    'CommandType', 'CommandStatus',
]

_MODULES = {
    'OntologyGenerator': 'ontology_generator',
    'GraphBuilderService': 'graph_builder',
    'TextProcessor': 'text_processor',
    'ZepEntityReader': 'zep_entity_reader', 'EntityNode': 'zep_entity_reader',
    'FilteredEntities': 'zep_entity_reader',
    'OasisProfileGenerator': 'oasis_profile_generator', 'OasisAgentProfile': 'oasis_profile_generator',
    'SimulationManager': 'simulation_manager', 'SimulationState': 'simulation_manager',
    'SimulationStatus': 'simulation_manager',
    'SimulationConfigGenerator': 'simulation_config_generator',
    'SimulationParameters': 'simulation_config_generator',
    'AgentActivityConfig': 'simulation_config_generator',
    'TimeSimulationConfig': 'simulation_config_generator',
    'EventConfig': 'simulation_config_generator', 'PlatformConfig': 'simulation_config_generator',
    'SimulationRunner': 'simulation_runner', 'SimulationRunState': 'simulation_runner',
    'RunnerStatus': 'simulation_runner', 'AgentAction': 'simulation_runner',
    'RoundSummary': 'simulation_runner',
    'ZepGraphMemoryUpdater': 'zep_graph_memory_updater',
    'ZepGraphMemoryManager': 'zep_graph_memory_updater',
    'AgentActivity': 'zep_graph_memory_updater',
    'SimulationIPCClient': 'simulation_ipc', 'SimulationIPCServer': 'simulation_ipc',
    'IPCCommand': 'simulation_ipc', 'IPCResponse': 'simulation_ipc',
    'CommandType': 'simulation_ipc', 'CommandStatus': 'simulation_ipc',
}


def __getattr__(name):
    module = _MODULES.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module(f'.{module}', __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
