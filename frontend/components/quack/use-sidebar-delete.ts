"use client";

import { usePathname, useRouter } from "next/navigation";
import { useData } from "@/lib/api/data";
import { useShell } from "./app-shell";

/**
 * Deleting from the left panel: the request, then — if the open page was the deleted item or lived in it — a step to the
 * nearest parent (everything lives under the graph of the subject, «/»).
 */
export function useSidebarDelete(activeDomainId: string) {
  const { removeItem } = useData();
  const { graphMode, setGraphMode, closeDrawer } = useShell();
  const router = useRouter();
  const pathname = usePathname();

  const leave = () => {
    closeDrawer();
    router.push("/");
  };

  return {
    domain: async (id: string) => {
      await removeItem("domain", id);
      // the open subject is gone: its pages (topics, chapters, sessions) go with it
      if (id === activeDomainId && pathname !== "/") leave();
    },
    hub: async (id: string) => {
      await removeItem("hub", id);
      if (graphMode.kind === "hub" && graphMode.hubId === id) setGraphMode({ kind: "overview" });
      if (pathname === `/hub/${id}`) leave();
    },
    topic: async (id: string, how: "hide" | "erase") => {
      await removeItem(how === "hide" ? "topic-hide" : "topic-erase", id);
      if (pathname === `/topic/${id}`) leave();
    },
    session: async (id: string) => {
      await removeItem("session", id);
      if (pathname === `/session/${id}`) leave();
    },
  };
}
