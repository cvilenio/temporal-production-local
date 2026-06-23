from orders_kernel.activities.customer_message import (
    make_customer_message_activities as make_customer_message_activities,
)
from orders_kernel.activities.external import (
    make_external_activities as make_external_activities,
)
from orders_kernel.activities.persistence import (
    make_persistence_activities as make_persistence_activities,
)

# We no longer export ALL_ACTIVITIES here since they require instantiated dependencies.
# The worker will call these factory functions to obtain the instantiated activities.
