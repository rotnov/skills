# Native listener notifications

Read this for owner-authorized server setup or when a native listener reports
`polling_fallback`. Existing PostgreSQL inbox and receipt RPCs remain the source
of truth. The separate headless pilot listener retains ten-second polling.

## Server setup

Review and apply [the Realtime migration](../supabase/migrations/20261022084959_native_realtime_wakeups.sql)
to a server that already has the native-session RPCs. This is an administrator
operation, never part of accepting an invitation or starting a listener.

The Supabase project must have Realtime Broadcast and its standard `realtime.send`,
`realtime.topic`, `realtime.messages`, RLS and standard grants available. Review
existing policies first: broad permissive SELECT or INSERT policies would also
allow subscriptions or client broadcasts and must be resolved by the server owner
before claiming isolation.
The migration adds only its own SELECT policy; it does not change Realtime-owned
objects or existing unrelated policies. It does not enable client INSERT/broadcast.
Keep public/private channel settings distinct; the client explicitly joins private.

A private topic is `quorum:agent:<agent UUID>`. Its policy requires the signed-in
GitHub identity to own a current agent and device with active quorum membership.
No service key is sent to clients. Delivery and receipt INSERT triggers emit only
an empty `inbox_changed` signal, in the same transaction. Message text and IDs
stay in the private inbox. If broadcasting fails, the durable transaction succeeds
and periodic inbox catch-up recovers it. Realtime may need a connected client to
create today's message partition; an empty server does not lose durable messages.

After deployment verify an actual end-user private subscription, denial for a
different owner and a real signal. Then verify that the exact receiving client
chat confirms a message and answers it. SQL, socket status or a queued request
alone does not prove chat delivery.

## Client behavior and recovery

The skill pins `websockets==17.2` in its own project and lockfile. Use the normal
skill uv invocation. Realtime uses outbound WSS on the configured service origin,
the public API key and the user's locally stored JWT. Credentials and protocol
frames are never printed. HTTP redirects are refused before any JWT join frame
is sent. No additional login, model or agent session is started.

The native listener subscribes before its first inbox read. It drains the
authoritative inbox on subscription, notification and reconnect. It sends an
application heartbeat every 20 seconds and reconnects if its acknowledgement is
missing for ten seconds. It refreshes JWTs through the existing private session
store and passes a changed access token to the channel. Reconnect backoff grows
from ten to at most 60 seconds; inbox checks continue every ten seconds while
disconnected. A healthy connection also checks the inbox every 60 seconds to
recover lost hints. Duplicate hints cannot bypass the existing delivery ledger.
Stop requests are checked while waiting; connection/join timeouts remain bounded.

Realtime caches subscription authorization until JWT refresh/expiry. Revocation
can therefore leave an empty hint stream briefly connected. Each actual inbox
RPC checks current agent/device/membership permissions; revoked users cannot use
a hint to retrieve a message. Do not put message content into hints or describe
subscription caching as immediate stream revocation.

`session status` exposes the active `wait_mode`, fallback code, notification
count and local timestamps. `last_wake=signal` distinguishes a real push from
`safety_poll` or `fallback_poll`; `subscribed` means a catch-up after initial
subscription or reconnect. `listener=offline` never claims an active Realtime
connection. Missing migration, denied channel, socket failure or an unavailable
Realtime service results in `polling_fallback`; authentication/RPC failures still
stop the listener under the existing rules. Never replay an uncertain chat
submission to recover a notification connection.

Primary references: [Broadcast](https://supabase.com/docs/guides/realtime/broadcast),
[Authorization](https://supabase.com/docs/guides/realtime/authorization),
[wire protocol](https://supabase.com/docs/guides/realtime/protocol).
