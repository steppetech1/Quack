"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type QueuedSend<TFile = unknown> = {
  id: string;
  text: string;
  files?: TFile[];
};

/**
 * Reusable FIFO send queue: holds outgoing messages `{ id, text, files? }` while a turn
 * is busy and dispatches the next item automatically once `busy` returns to `false`.
 */
export function useSendQueue<TFile = unknown>({
  busy,
  onSend,
}: {
  busy: boolean;
  onSend: (item: QueuedSend<TFile>) => void | boolean | Promise<boolean | void>;
}) {
  const [items, setItems] = useState<QueuedSend<TFile>[]>([]);
  const seq = useRef(0);
  const onSendRef = useRef(onSend);
  const dispatchedId = useRef<string | null>(null);

  useEffect(() => {
    onSendRef.current = onSend;
  });

  const enqueue = useCallback((text: string, files?: TFile[]): QueuedSend<TFile> => {
    const item: QueuedSend<TFile> = {
      id: `q-${Date.now()}-${++seq.current}`,
      text,
      ...(files?.length ? { files } : {}),
    };
    setItems((prev) => [...prev, item]);
    return item;
  }, []);

  const remove = useCallback((id: string) => {
    setItems((prev) => prev.filter((x) => x.id !== id));
  }, []);

  const clear = useCallback(() => {
    setItems([]);
    dispatchedId.current = null;
  }, []);

  useEffect(() => {
    if (busy || items.length === 0) return;
    const next = items[0];
    const timer = setTimeout(() => {
      if (dispatchedId.current === next.id) return;
      dispatchedId.current = next.id;
      setItems((prev) => prev.filter((x) => x.id !== next.id));
      void onSendRef.current(next);
    }, 0);
    return () => clearTimeout(timer);
  }, [busy, items]);

  return { items, enqueue, remove, clear };
}
