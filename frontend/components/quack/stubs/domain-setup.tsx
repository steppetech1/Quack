"use client";

import { useRouter } from "next/navigation";
import { API_MODE } from "@/lib/api/client";
import { NewDomainLive, NewDomainScreen } from "../live/domain-setup-live";

/** New subject on mocks: the same card; «Create» just opens the app (the backend flow is NewDomainLive) */
function NewDomainMock() {
  const router = useRouter();
  return <NewDomainScreen onCreate={() => router.push("/")} busy={false} error={null} onClearError={() => {}} />;
}

export function NewDomainStub() {
  return API_MODE ? <NewDomainLive /> : <NewDomainMock />;
}
