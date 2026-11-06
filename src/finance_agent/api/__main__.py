import os

from finance_agent.api import DEFAULT_PORT, serve

serve(int(os.environ.get("FA_PORT", DEFAULT_PORT)))
