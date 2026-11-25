"use client";

import { SkillDraftForm } from "../skill-proposal";
import { DocScroll } from "./doc-scroll";
import type { WorkspaceDoc } from "./workspace-context";

/**
 * A skill the teacher proposed in the chat, opened beside it. It is the same draft as the card in the chat (the learner's
 * changes are shared) with the same fields and the same Save; nothing is saved until the learner presses it.
 */
export function SkillDraftDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "skill_draft" }> }) {
  return (
    <DocScroll>
      <SkillDraftForm draftId={doc.id} base={doc.base} inPane />
    </DocScroll>
  );
}
