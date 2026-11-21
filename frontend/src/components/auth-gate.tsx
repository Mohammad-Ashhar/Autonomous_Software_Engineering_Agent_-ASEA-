"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  getStoredSession,
  isSupabaseAuthConfigured,
  signIn,
  signOut,
  signUp,
  type SupabaseSession,
} from "@/lib/auth";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SupabaseSession | null>(null);
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const configured = isSupabaseAuthConfigured();

  useEffect(() => {
    setSession(getStoredSession());
    setReady(true);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      if (mode === "signin") {
        setSession(await signIn(email.trim(), password));
        setMessage("Signed in successfully.");
      } else {
        const nextSession = await signUp(email.trim(), password);
        if (nextSession) {
          setSession(nextSession);
          setMessage("Account created and signed in.");
        } else {
          setMessage("Account created. Check your email to confirm it, then sign in.");
          setMode("signin");
        }
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!configured) return <>{children}</>;
  if (!ready) return <main className="auth-screen"><p>Loading secure session…</p></main>;
  if (session) {
    return (
      <>
        <div className="auth-session-bar">
          <span>Signed in{session.user?.email ? ` as ${session.user.email}` : ""}</span>
          <button type="button" onClick={() => { signOut(); setSession(null); }}>Sign out</button>
        </div>
        {children}
      </>
    );
  }

  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand">ASEA</div>
        <h1>{mode === "signin" ? "Welcome back" : "Create your account"}</h1>
        <p>Sign in to securely access your repair jobs and workspace.</p>
        <label htmlFor="asea-auth-email">Email</label>
        <input id="asea-auth-email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
        <label htmlFor="asea-auth-password">Password</label>
        <input id="asea-auth-password" type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} minLength={8} required value={password} onChange={(event) => setPassword(event.target.value)} />
        {message && <p className="auth-message" role="status">{message}</p>}
        <button className="auth-submit" type="submit" disabled={busy}>{busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}</button>
        <button className="auth-switch" type="button" onClick={() => { setMode(mode === "signin" ? "signup" : "signin"); setMessage(""); }}>
          {mode === "signin" ? "New to ASEA? Create an account" : "Already have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}
