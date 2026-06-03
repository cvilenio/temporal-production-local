import secrets
import time

# Crockford Base32 alphabet (excludes I, L, O, U)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(n: int, length: int) -> str:
    """Encode an integer into a Crockford Base32 string of fixed length."""
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))


def generate_order_id() -> str:
    """
    Generate a customer-friendly, time-sortable ID.
    Format: ORD-{16 chars of Crockford Base32}
    Total length: 20 characters.
    
    Structure:
    - First 10 chars: 48-bit millisecond timestamp (time-sortable until year 10889)
    - Last 6 chars: 32-bit random value (~4.3 billion per ms collision space)
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand = secrets.randbits(32)  # 32 bits
    
    # 80 bits total = 16 chars * 5 bits
    combined = (ts_ms << 32) | rand
    
    return f"ORD-{_encode_crockford(combined, 16)}"


def generate_order_ids() -> tuple[str, str]:
    """
    Generates order_id and workflow_id.
    Returns: (order_id, workflow_id)
    """
    order_id = generate_order_id()
    # We maintain workflow_id as a separate field for architectural decoupling,
    # though it currently mirrors the order_id.
    return order_id, order_id
