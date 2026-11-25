import { upload } from "./client";
import type { MaterialView } from "./materials";

/**
 * Files chosen on the «new subject» card go to the subject AFTER it is created (an upload needs `domain_id`). The learner is
 * already on the subject's page by then, so the queue lives here, outside any component: it keeps running while pages change,
 * and the page of the subject reads it (with `useSyncExternalStore`) to show the agent lines.
 *
 * One file at a time (`POST /v1/domains/{id}/materials`, 201 with the material in `uploaded`); after that the backend works
 * on the material by itself and the page follows its status. A failed file is marked and the queue goes on.
 */

/** Files one «new subject» card may carry */
export const MAX_CONTEXT_FILES = 15;

export type UploadState = "queued" | "uploading" | "sent" | "failed";
export type UploadItem = {
  id: string;
  domainId: string;
  name: string;
  state: UploadState;
  /** The material the backend made (state `sent`) */
  material?: MaterialView;
};

let items: UploadItem[] = [];
const listeners = new Set<() => void>();
const waiting: { id: string; file: File }[] = [];
let draining = false;

const emit = () => listeners.forEach((cb) => cb());

function change(id: string, patch: Partial<UploadItem>) {
  items = items.map((i) => (i.id === id ? { ...i, ...patch } : i));
  emit();
}

export function subscribeUploads(cb: () => void) {
  listeners.add(cb);
  return () => void listeners.delete(cb);
}

/** The same array until something changes, so `useSyncExternalStore` does not loop */
export const getUploads = () => items;
const NONE: UploadItem[] = [];
export const getUploadsServer = () => NONE;

let seq = 0;

async function drain() {
  if (draining) return;
  draining = true;
  try {
    for (let next = waiting.shift(); next; next = waiting.shift()) {
      const item = items.find((i) => i.id === next.id);
      if (!item) continue;
      change(item.id, { state: "uploading" });
      try {
        const form = new FormData();
        form.append("file", next.file);
        const made = await upload<MaterialView>(`/v1/domains/${item.domainId}/materials`, form);
        change(item.id, { state: "sent", material: made });
      } catch {
        change(item.id, { state: "failed" });
      }
    }
  } finally {
    draining = false;
  }
}

/** Queues the files for the subject and starts sending; returns at once */
export function uploadInBackground(domainId: string, files: File[]) {
  if (!files.length) return;
  const made: UploadItem[] = files.map((f) => ({ id: `up-${Date.now()}-${++seq}`, domainId, name: f.name, state: "queued" }));
  items = [...items, ...made];
  made.forEach((m, i) => waiting.push({ id: m.id, file: files[i] }));
  emit();
  void drain();
}

/**
 * Development only (mocks have no backend): plays the way a real upload goes, so the agent lines and the growing graph can
 * be looked at. `grow` is called when a file reaches «building the graph».
 */
export function simulateUploads(domainId: string, names: string[], grow?: (name: string, index: number) => void) {
  const base = Date.now();
  names.forEach((name, i) => {
    const id = `sim-${base}-${++seq}`;
    const material = (status: string): MaterialView => ({
      material_id: id,
      domain_id: domainId,
      session_id: null,
      mime: "application/pdf",
      status,
      pages_count: null,
      created_at: new Date(base).toISOString(),
      title: name,
    });
    items = [...items, { id, domainId, name, state: "queued" }];
    emit();
    const step = (ms: number, fn: () => void) => setTimeout(fn, ms + i * 900);
    step(300, () => change(id, { state: "uploading" }));
    step(1200, () => change(id, { state: "sent", material: material("uploaded") }));
    step(3200, () => {
      change(id, { material: material("read") });
      grow?.(name, i);
    });
    step(8200, () => change(id, { material: material("parsed") }));
  });
}
