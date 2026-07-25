"""Fail-closed exceptions raised by Knowledge Kernel contracts."""


class KernelContractError(ValueError):
    """Base class for contract validation failures."""


class ContractValidationError(KernelContractError):
    """A record violates a structural or semantic contract invariant."""


class AuthorityViolation(KernelContractError):
    """An actor attempted an operation outside its declared authority."""


class OwnershipViolation(KernelContractError):
    """A tenant or user attempted to cross an ownership boundary."""


class InvalidTransition(KernelContractError):
    """A lifecycle transition is not present in the explicit state graph."""


class QuotaExceeded(KernelContractError):
    """A Personal Memory HAT pool operation would exceed its policy."""


class IntegrityError(KernelContractError):
    """A deterministic hash or immutable binding does not verify."""
