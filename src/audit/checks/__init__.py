
from .content_checks import run as run_content_checks
from .forms_checks import run as run_forms_checks
from .labeling_checks import run as run_labeling_checks
from .navigation_checks import run as run_navigation_checks
from .feedback_checks import run as run_feedback_checks

__all__ = [
    "run_content_checks",
    "run_forms_checks",
    "run_labeling_checks",
    "run_navigation_checks",
    "run_feedback_checks",
]
