"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { API_MODE, ApiError, post, setToken, type S } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { Duck } from "../duck";
import { FIELD, Field, PRIMARY_BTN, StubNote } from "./stub-parts";

/** Log in / sign up. With the backend it gets a JWT; on mocks submitting just opens the app. */
export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const t = useT().stubs.auth;
  const router = useRouter();
  const id = useId();
  const signup = mode === "signup";
  const tx = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (form: FormData) => {
    if (!API_MODE) {
      router.push("/");
      return;
    }
    setBusy(true);
    setError(null);
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    const name = String(form.get("name") ?? "").trim();
    try {
      const res = signup
        ? await post("/v1/auth/register", { email, password, display_name: name || null })
        : await post("/v1/auth/login", { email, password });
      setToken(res.token);
      // the app layout mounts fresh and loads data with the new token
      router.replace("/");
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      setError(
        code === "UNAUTHORIZED"
          ? tx.live.badLogin
          : e instanceof ApiError && e.status === 409
            ? tx.live.emailTaken
            : code === "RATE_LIMITED"
              ? tx.live.rateLimited
              : e instanceof ApiError && e.status < 500
                ? e.message
                : tx.live.serverDown,
      );
      setBusy(false);
    }
  };

  return (
    <main className="flex h-full overflow-y-auto bg-cloth px-4 py-10">
      <div className="m-auto w-full max-w-[400px]">
        <Duck className="size-14" />
        <h1 className="mt-4 font-serif text-[28px] leading-tight font-bold text-paper">{signup ? t.signupTitle : t.loginTitle}</h1>
        <p className="mt-2 text-body text-paper-dim">{signup ? t.signupBody : t.loginBody}</p>

        <form
          className="mt-2"
          onSubmit={(e) => {
            e.preventDefault();
            void submit(new FormData(e.currentTarget));
          }}
        >
          {signup && (
            <Field id={`${id}-name`} label={t.name}>
              <input id={`${id}-name`} name="name" autoComplete="nickname" placeholder={t.namePh} className={FIELD} />
            </Field>
          )}
          <Field id={`${id}-email`} label={t.email}>
            <input id={`${id}-email`} name="email" type="email" required autoComplete="email" placeholder={t.emailPh} className={FIELD} />
          </Field>
          <Field id={`${id}-pw`} label={t.password} help={signup ? t.passwordHelp : undefined}>
            <input
              id={`${id}-pw`}
              name="password"
              type="password"
              required
              minLength={signup ? 8 : 1}
              autoComplete={signup ? "new-password" : "current-password"}
              className={FIELD}
            />
          </Field>
          {error && (
            <p role="alert" className="mt-4 text-small text-st-confused">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy} className={`${PRIMARY_BTN} mt-6 w-full`}>
            {signup ? t.signup : t.login}
          </button>
        </form>

        <p className="mt-5 text-small text-paper-dim">
          {signup ? t.haveAccount : t.noAccount}{" "}
          <Link href={signup ? "/login" : "/signup"} className="text-paper underline decoration-seam underline-offset-4 hover:decoration-paper-dim">
            {signup ? t.toLogin : t.toSignup}
          </Link>
        </p>

        {!API_MODE && <StubNote className="mt-8" endpoints={signup ? ["POST /v1/auth/register"] : ["POST /v1/auth/login", "GET /v1/auth/me"]} />}
      </div>
    </main>
  );
}
