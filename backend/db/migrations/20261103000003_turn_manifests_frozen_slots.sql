-- Slots 3 and 4 of the teacher context frozen at the session start (Т-§10.1, WP14.1; D-A-137):
-- `{profile, domain_and_goals}` rendered once and read from the earliest manifest of the
-- session that has them. NULL for later turns and for turns before the freeze.

-- migrate:up
ALTER TABLE ops.turn_manifests ADD COLUMN frozen_slots JSONB NULL;

-- migrate:down
ALTER TABLE ops.turn_manifests DROP COLUMN frozen_slots;
