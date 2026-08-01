"""German Law HAT package and source-authority policy 1A."""
from .adapters import *
from .errors import GermanLawPolicyError
from .hat import GermanLawHat, request_from_mapping
from .models import *
from .policy import assess_source, assess_temporal, authority_sort_key
