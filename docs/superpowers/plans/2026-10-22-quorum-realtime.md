# Quorum native listener notifications

> Implementation method: delegated agent, as requested by the owner.

**Goal:** Wake existing Codex and Claude chats when their durable inbox changes.

**Approved design:** Supabase private Realtime Broadcast carries an empty wake
signal for one agent. Existing authenticated PostgreSQL RPCs remain authoritative
for messages and receipts. Native listeners reconcile on subscription/reconnect,
check every 60 seconds while connected, and fall back to ten-second checks while
notifications are unavailable. Headless pilot handling remains unchanged.

**Constraints:** Preserve exact-session binding, permission boundaries, explicit
UI receipts and uncertain-submission handling. Do not launch a model or modify a
live listener. Do not change the native `handled` counter in this patch. No live
DDL or publication until independent review and parent synchronization.

## Implementation and checks

- [x] Write failing tests for private join, signal wake, reconnect catch-up,
  heartbeat acknowledgement, refreshed JWT, bounded stop and polling fallback.
  Use a real local WebSocket server and synthetic credentials.
- [x] Implement `scripts/quorum_realtime.py` with pinned `websockets` and bounded
  protocol 1.0 frames; integrate its wait callback only into native sessions.
  Preserve the listener loop's normal delivery/unknown paths.
- [x] Write failing disposable PostgreSQL tests for own/foreign/revoked channel
  access, receive-only grants, transactional empty signals, receipt wake and
  notification failure preserving the durable message.
- [x] Add an owner-applied migration with a narrowly scoped authorization helper,
  SELECT policy and notification triggers. Keep privileged helpers private with
  fixed search paths and no implicit PUBLIC execution.
- [x] Document installation, server migration, actual transport status, fallback
  and Realtime authorization caching. Run the root tests, backend tests, locked
  dependency checks, both official skill gates and complete pre-commit.
- [ ] Obtain independent review; publish only after parent approval. A fixture
  does not establish delivery to a real owner chat.

## Review focus

- Signals arrive between an empty RPC and socket subscription: subscribe then
  catch up; never depend on delivery of every signal.
- A quiet broken connection: require application heartbeat acknowledgement and
  bounded reconnect; always retain periodic durable catch-up.
- Revocation during a cached subscription: transmit no message content, and
  enforce current authorization on every inbox read.
- Native Claude receipt unblocks the next durable message: receipt insertion
  signals the same agent without changing receipt accounting.
- Broadcasting unavailable or its daily partition missing: notification errors
  must not roll back the authoritative message transaction.
