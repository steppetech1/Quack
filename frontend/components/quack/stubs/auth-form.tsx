"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { API_MODE, ApiError, post } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { AuthScene } from "../auth/auth-scene";
import { StubNote } from "./stub-parts";

/** Поле входа: тонкая рамка на тёмной подложке, фокус — оранжевая рамка и кольцо */
const INPUT =
  "h-12 w-full rounded-control border border-white/12 bg-[#100906] px-4 text-body text-[#f6eadc] transition-colors duration-(--dur-s) ease-out placeholder:text-[#bba995]/60 hover:border-white/25 focus-visible:border-[#ff8a2a] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ff8a2a]/30";

/**
 * Log in / sign up. With the backend there is no password: a one-time link by email (`POST /auth/link`) or Google
 * (`GET /auth/google/start`); the session is the `quack_session` cookie. On mocks submitting just opens the app.
 */
export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const t = useT().stubs.auth;
  const live = useT().live;
  const router = useRouter();
  const id = useId();
  const signup = mode === "signup";
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The duck covers its eyes while the password is being typed
  const [blind, setBlind] = useState(false);

  const submit = async (form: FormData) => {
    if (!API_MODE) {
      router.push("/");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post("/auth/link", { email: String(form.get("email") ?? "").trim() });
      setSent(true);
    } catch (e) {
      setError(e instanceof ApiError && e.code === "RATE_LIMITED" ? live.rateLimited : e instanceof ApiError && e.status < 500 ? e.message : e instanceof ApiError ? live.serverError : live.serverDown);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="relative isolate h-full overflow-y-auto bg-[#080504] text-[#f6eadc]">
      {/* фон на весь экран: утка из глифов, графы по бокам, плавающие ноды; сцена не прокручивается вместе с формой */}
      <AuthScene blind={blind} className="fixed inset-0 -z-20" />
      {/* тёмная подложка справа: глифы под формой не мешают читать поля */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 -z-10 bg-[linear-gradient(to_bottom,transparent_0%,#080504d9_46%)] lg:bg-[linear-gradient(to_left,#080504a6_0%,#080504a6_30%,transparent_62%)]"
      />
      <div className="mx-auto grid min-h-full max-w-[1400px] lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <section className="relative h-[40svh] min-h-60 lg:h-auto">
          <p className="display-caps absolute left-6 top-6 text-[1.375rem] text-[#f6eadc] lg:left-10 lg:top-8">
            Qvack<span className="text-[#ff8a2a]">!</span>
          </p>
        </section>

        <section className="flex px-6 pb-12 pt-4 lg:px-14 lg:py-10">
          <div className="m-auto w-full max-w-[400px]">
            <h1 className="display-caps text-[clamp(2rem,4.2vw,3rem)] leading-[1.02] text-[#f6eadc] [text-shadow:0_0_40px_#ff7a1a40]">
              {signup ? t.signupTitle : t.loginTitle}
            </h1>
            <p className="mt-3 text-body text-[#bba995]">{API_MODE ? live.linkBody : signup ? t.signupBody : t.loginBody}</p>

            {sent ? (
              <p role="status" className="mt-8 text-body text-[#f6eadc]">
                {live.linkSent}
              </p>
            ) : (
              <form
                className="mt-8 grid gap-5"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit(new FormData(e.currentTarget));
                }}
              >
                {signup && !API_MODE && (
                  <AuthField id={`${id}-name`} label={t.name}>
                    <input id={`${id}-name`} name="name" autoComplete="nickname" placeholder={t.namePh} className={INPUT} />
                  </AuthField>
                )}
                <AuthField id={`${id}-email`} label={t.email}>
                  <input id={`${id}-email`} name="email" type="email" required autoComplete="email" placeholder={t.emailPh} className={INPUT} />
                </AuthField>
                {/* with the backend there is no password: the link in the email is the key */}
                {!API_MODE && (
                  <AuthField id={`${id}-pw`} label={t.password} help={signup ? t.passwordHelp : undefined}>
                    <input
                      id={`${id}-pw`}
                      name="password"
                      type="password"
                      required
                      minLength={signup ? 8 : 1}
                      autoComplete={signup ? "new-password" : "current-password"}
                      onFocus={() => setBlind(true)}
                      onBlur={() => setBlind(false)}
                      className={INPUT}
                    />
                  </AuthField>
                )}
                {error && (
                  <p role="alert" className="text-small text-st-confused">
                    {error}
                  </p>
                )}
                <button
                  type="submit"
                  disabled={busy}
                  className="mt-2 inline-flex h-12 w-full items-center justify-center rounded-pill bg-[#ff8a2a] text-body font-semibold text-[#1a0a02] shadow-[0_0_32px_#ff7a1a40] transition-colors duration-(--dur-s) ease-out hover:bg-[#ff9d4d] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ff8a2a] disabled:opacity-50"
                >
                  {busy ? live.linkSending : API_MODE ? live.linkSend : signup ? t.signup : t.login}
                </button>
                {API_MODE && (
                  <>
                    <p className="text-center text-small text-[#bba995]">{live.orWord}</p>
                    {/* a full page navigation on purpose: the backend answers with a redirect to Google */}
                    <a
                      href="/api/auth/google/start"
                      className="inline-flex h-12 w-full items-center justify-center rounded-pill border border-white/12 text-body font-semibold text-[#f6eadc] transition-colors duration-(--dur-s) ease-out hover:border-white/25 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ff8a2a]"
                    >
                      {live.google}
                    </a>
                  </>
                )}
              </form>
            )}

            <p className="mt-6 text-small text-[#bba995]">
              {signup ? t.haveAccount : t.noAccount}{" "}
              <Link
                href={signup ? "/login" : "/signup"}
                className="font-medium text-[#ff8a2a] underline decoration-[#ff8a2a]/40 underline-offset-4 transition-colors duration-(--dur-s) ease-out hover:decoration-[#ff8a2a]"
              >
                {signup ? t.toLogin : t.toSignup}
              </Link>
            </p>

            {!API_MODE && <StubNote className="mt-8" endpoints={signup ? ["POST /v1/auth/register"] : ["POST /v1/auth/login", "GET /v1/auth/me"]} />}
          </div>
        </section>
      </div>
    </main>
  );
}

/** Подпись над полем и необязательная подсказка под ним */
function AuthField({ id, label, help, children }: { id: string; label: string; help?: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="caps mb-2 block text-[#bba995]">
        {label}
      </label>
      {children}
      {help && <p className="mt-2 text-small text-[#bba995]">{help}</p>}
    </div>
  );
}
