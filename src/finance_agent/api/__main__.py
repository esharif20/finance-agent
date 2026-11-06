import os

from finance_agent.api import serve
from finance_agent.identity import DEFAULT_PORT

serve(int(os.environ.get("FA_PORT", DEFAULT_PORT)))
