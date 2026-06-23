import hashlib

# Crockford Base32 alphabet (excludes I, L, O, U)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(n: int, length: int) -> str:
    """Encode an integer into a Crockford Base32 string of fixed length."""
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))


def order_id_from_key(idempotency_key: str) -> str:
    """
    Deterministic, customer-friendly order id derived from the client
    idempotency key. The same key always yields the same id, so a retried
    submission maps to one order (and one workflow, since workflow_id == order_id).

    Format: ORD-{16 chars of Crockford Base32} (80 bits of a SHA-256 digest).
    Not time-sortable, unlike a random id — ordering is done via timestamp
    columns, not the id itself.
    """
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    # 80 bits = 10 bytes = 16 Crockford chars
    n = int.from_bytes(digest[:10], "big")
    return f"ORD-{_encode_crockford(n, 16)}"
