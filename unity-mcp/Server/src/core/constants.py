"""Server-wide protocol constants."""

# HTTP header name for API key authentication
API_KEY_HEADER = "X-API-Key"

# Environment variable carrying the shared secret that guards the local REST
# control plane (/api/*) and the streamable-http transport when the server is
# NOT remote-hosted. Passed via the environment rather than a CLI flag because
# argv is world-readable via `ps`.
LOCAL_API_TOKEN_ENV = "UNITY_MCP_LOCAL_API_TOKEN"

# Base path of the streamable-http MCP transport, and the whole path: the shared
# secret travels in the X-API-Key header, never in the URL.
#
# It used to be appended as a path segment (-> /mcp/<secret>). That form was
# removed on 2026-07-27 because a secret in the URL leaks into places a URL is
# allowed to go: the generated client configs (including one the model itself can
# read), the argv of the registration commands, and error logs. Clients were
# measured to support headers before the switch -- see core/local_auth.py.
MCP_TRANSPORT_BASE_PATH = "/mcp"

# Request-level default Unity instance for the MCP transport: a client can pin
# routing for everything it sends by putting the instance in its connection
# URL (/mcp?instance=Name@hash) or in this header. There is no server-side
# per-session pin: the 2026-07-28 protocol has no session to pin to, and the
# old pin fell back to one process-global key, so one client's
# set_active_instance re-routed every other client.
UNITY_INSTANCE_HEADER = "X-Unity-Instance"
UNITY_INSTANCE_QUERY_PARAM = "instance"

# The Gamachine conversation a caller works for, so its approval card is
# attributed to that chat. A connection states it once (header or ?conv= on the
# URL); a client sharing one session across chats puts it in each call's _meta.
# Never read from tool arguments: the model writes those.
GAMACHINE_CONVERSATION_HEADER = "X-Gamachine-Conversation"
GAMACHINE_CONVERSATION_QUERY_PARAM = "conv"
GAMACHINE_CONVERSATION_META_KEY = "gamachine_conversation"
