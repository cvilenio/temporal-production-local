from enum import StrEnum


class ErrorType(StrEnum):
    """Typed error identifiers raised by activities and caught by workflows.
    Kept as StrEnum so values travel through Temporal's ApplicationError.type
    without requiring custom class deserialization.
    """

    SHIPMENT_NOT_VERIFIED = "ShipmentNotVerified"
    COMPENSATION_FAILED = "CompensationFailed"
    UNEXPECTED_ORDER_FAILURE = "UnexpectedOrderFailure"
    UNRECOGNIZED_ACTIVITY_FAILURE = "UnrecognizedActivityFailure"
    # Activity received a contract_version it does not support (ADR-0021).
    CONTRACT_VERSION_UNSUPPORTED = "ContractVersionUnsupported"
