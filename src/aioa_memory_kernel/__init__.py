"""Domain-neutral contract foundation for the AIOA Memory Patch kernel.

The package deliberately exposes data contracts and deterministic state
transitions only. It does not load plugins, retrieve evidence, call models,
perform external actions, or connect to a persistence service.
"""

from .contracts import *  # noqa: F401,F403 - intentional public facade
from .contracts import __all__ as _contract_exports
from .state_machines import *  # noqa: F401,F403 - intentional public facade
from .state_machines import __all__ as _state_machine_exports

__all__ = [*_contract_exports, *_state_machine_exports]
