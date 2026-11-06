"""Write the API's OpenAPI schema to stdout (input to `openapi-typescript`)."""

import json
from typing import cast

from sqlalchemy import Engine

from finance_agent.api import create_app

# the schema never touches the database, so no engine (or Keychain) is needed
print(json.dumps(create_app(cast(Engine, None)).openapi(), indent=2, sort_keys=True))
