"""German Law HAT package and source-authority policy 1A."""
from .adapters import *
from .errors import GermanLawPolicyError
from .hat import GermanLawHat, request_from_mapping
from .models import *
from .policy import assess_source, assess_temporal, authority_sort_key
from .normalization import *
from .publication import *
from .corpus import (
    STEP14_HAT_SCOPE_ID,
    STEP14_MAPPING_VERSION,
    STEP14_TENANT_ID,
    build_source_registry_record,
    registration_operation_identity,
)
