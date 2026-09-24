import json

from app.main import app as fastapi_app


# TEMP diagnostic: expose what path Vercel hands the app.
async def app(scope, receive, send):
    if scope["type"] != "http":
        return await fastapi_app(scope, receive, send)
    info = json.dumps({
        "path": scope.get("path"),
        "root_path": scope.get("root_path"),
        "raw_path": (scope.get("raw_path") or b"").decode(),
        "query": (scope.get("query_string") or b"").decode(),
        "headers": {k.decode(): v.decode() for k, v in scope.get("headers", []) if k.decode().startswith("x-") and "ip" not in k.decode() and "sig" not in k.decode()},
    })[:4000]

    async def send_wrapper(message):
        if message["type"] == "http.response.start":
            message.setdefault("headers", [])
            message["headers"] = list(message["headers"]) + [(b"x-debug-scope", info.encode())]
        await send(message)

    return await fastapi_app(scope, receive, send_wrapper)
