"use client";

import { useMocks } from "@/lib/i18n/provider";
import { DemoFrame } from "../live/demo-panel";
import { mockDemoHtml } from "./mock-demo";
import type { WorkspaceDoc } from "./workspace-context";

/** A demo of the plan: the frame fills the whole pane. In mock mode the page is drawn from the app itself (`srcDoc`) */
export function DemoDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "demo" }> }) {
  const { workspace } = useMocks();
  return <DemoFrame demo={{ requestId: doc.id, title: doc.title }} srcDoc={doc.mock ? mockDemoHtml(workspace.demoLabels) : undefined} />;
}
