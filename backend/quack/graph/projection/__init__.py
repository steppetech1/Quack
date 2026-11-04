"""Projector of Phase 4, `rules@3` (ТЗ Ф4 WP3; Т-§2.4, Т-§2.5).

Handlers `handle(event, view) -> mutations` are pure and declare `needs(event)`; the view is the
slice read before the batch plus the changes the batch already made. The Phase 3 projector was
removed in WP1.3 (D-A-234).
"""
