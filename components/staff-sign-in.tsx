"use client";

import { useEffect, useState } from "react";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { staffJson, type StaffSession } from "@/lib/staff-fetch";

export default function StaffSignIn({
  onSignedIn,
}: {
  onSignedIn: (session: StaffSession) => Promise<void>;
}) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    staffJson<{ configured: boolean }>("/api/auth/config")
      .then((data) => setConfigured(data.configured))
      .catch(() => setError("Sign-in is unavailable. Please try again later."));
  }, []);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session = await staffJson<StaffSession>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      setPassword("");
      await onSignedIn(session);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="admin-login" onSubmit={submit}>
      <ShieldCheck size={27} />
      <h3>Staff sign-in</h3>
      <p>Use your college staff account to review and publish documents.</p>
      {configured === false && (
        <p role="alert">
          Staff sign-in is not configured. Contact your administrator.
        </p>
      )}
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      <label htmlFor="staff-email">Email address</label>
      <input
        id="staff-email"
        type="email"
        autoComplete="username"
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
      <label htmlFor="staff-password">Password</label>
      <input
        id="staff-password"
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <button className="primary-button" disabled={busy || configured !== true}>
        {busy ? "Signing in…" : "Sign in"}
        <ArrowRight size={16} />
      </button>
    </form>
  );
}
