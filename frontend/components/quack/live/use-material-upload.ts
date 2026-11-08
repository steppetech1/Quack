"use client";

import { useCallback, useState } from "react";
import { ApiError, upload } from "@/lib/api/client";
import type { MaterialView } from "@/lib/api/materials";
import { useT } from "@/lib/i18n/provider";
import { useNotice } from "../soon";
import { useErrorText } from "./error-text";

/**
 * `POST /v1/domains/{id}/materials` (multipart, field `file`): a lecture or a photo into a subject. The backend answers 201
 * with the material in status `uploaded`; the parser then works on it in the background. Shared by the chapter page and the input box.
 * The result is the new material, or `null` when the upload failed (the toast already said why).
 */
export function useMaterialUpload(domainId: string, sessionId?: string) {
  const t = useT().live;
  const notice = useNotice();
  const errorText = useErrorText();
  const [uploading, setUploading] = useState(false);

  const send = useCallback(
    async (file: File): Promise<MaterialView | null> => {
      if (!domainId) return null;
      setUploading(true);
      try {
        const form = new FormData();
        form.append("file", file);
        if (sessionId) form.append("session_id", sessionId);
        const made = await upload<MaterialView>(`/v1/domains/${domainId}/materials`, form);
        notice(t.materialUploaded);
        return made;
      } catch (e) {
        notice(e instanceof ApiError && e.code === "UPLOAD_REJECTED" ? t.materialRejected : errorText(e));
        return null;
      } finally {
        setUploading(false);
      }
    },
    [domainId, sessionId, notice, errorText, t.materialUploaded, t.materialRejected],
  );

  return { send, uploading };
}
