"use client";

import { useEffect, useState } from "react";
import { API_MODE, ApiError, get, type S } from "./client";

/** How many sentences of the notes the hover card shows, and the cap in characters */
const SENTENCES = 2;
const MAX_CHARS = 240;

/** topic id → text (`null`: the topic has no notes yet). One request per topic for the whole visit */
const cache = new Map<string, string | null>();
const inflight = new Map<string, Promise<string | null>>();

/** The first sentences of the notes: no headings, tables or list markers; sentences with formulas only when there are no others */
export function blurbFromBlocks(blocks: { content?: unknown }[]): string | null {
  const text = blocks
    .map((b) => (typeof b.content === "string" ? b.content : ""))
    .join("\n")
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#") && !l.startsWith("|") && !/^[-*+]\s/.test(l) && !/^\d+\.\s/.test(l))
    .join(" ")
    .replace(/\*\*|__|`/g, "");
  const sentences = text.split(/(?<=[.!?])\s+/).filter(Boolean);
  const plain = sentences.filter((s) => !s.includes("$"));
  const pick = (plain.length ? plain : sentences.map((s) => s.replace(/\$/g, ""))).slice(0, SENTENCES).join(" ");
  if (!pick) return null;
  return pick.length > MAX_CHARS ? `${pick.slice(0, MAX_CHARS - 1).trimEnd()}…` : pick;
}

function load(id: string): Promise<string | null> {
  const known = inflight.get(id);
  if (known) return known;
  const p = get<S["ArtifactResponse"]>(`/v1/topics/${id}/notes`)
    .then((r) => blurbFromBlocks(r.blocks))
    .catch((e) => {
      // 404: no notes yet. Anything else: try again next time
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    })
    .then((v) => {
      cache.set(id, v);
      return v;
    })
    .finally(() => inflight.delete(id));
  inflight.set(id, p);
  return p;
}

/** The start of the topic's notes for the hover card; `null` while it loads, or when there are no notes (the card keeps its own line) */
export function useTopicBlurb(topicId: string): string | null {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!API_MODE || cache.has(topicId)) return;
    let off = false;
    load(topicId)
      .then(() => !off && bump((n) => n + 1))
      .catch(() => {});
    return () => {
      off = true;
    };
  }, [topicId]);
  return API_MODE ? (cache.get(topicId) ?? null) : null;
}
