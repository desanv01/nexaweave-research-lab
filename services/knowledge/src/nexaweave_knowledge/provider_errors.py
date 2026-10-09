"""SDK-independent provider failure identities, shared by read and model paths."""

class UnsupportedCapability(RuntimeError):
    pass


class ReconciliationRequired(RuntimeError):
    pass


class OperationConflict(RuntimeError):
    pass


class ScopeViolation(RuntimeError):
    pass


