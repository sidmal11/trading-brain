"""Lambda entry point for the read API.

Mangum adapts the ASGI app to the event shape API Gateway sends, so the same
`api.main:app` runs under uvicorn locally and behind API Gateway in AWS with no
branching in the application code.
"""

from mangum import Mangum

from .main import app

handler = Mangum(app, lifespan="off")
