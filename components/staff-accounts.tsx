"use client";

import { useEffect, useState } from "react";
import { staffJson, type StaffSession } from "@/lib/staff-fetch";

type Member = {
  id: string;
  email: string;
  role: "admin" | "staff";
  enabled: boolean;
};

export default function StaffAccounts({ session }: { session: StaffSession }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function load() {
    setMembers(await staffJson<Member[]>("/api/admin/staff"));
  }
  useEffect(() => {
    if (session.role === "admin")
      void load().catch((err) => setError(err.message));
  }, [session.role]);
  async function grant(email: string, role: string, enabled: boolean) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await staffJson("/api/admin/staff", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role, enabled }),
      });
      setMessage("Staff access updated.");
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function changePassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const password = String(fields.get("password"));
    if (password !== fields.get("confirm")) {
      setError("The passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await staffJson("/api/auth/password", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      form.reset();
      setMessage("Password updated.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="staff-accounts">
      <h3>Staff account</h3>
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {session.role === "admin" && (
        <>
          <p>
            Staff can manage documents. Administrators can also manage staff
            access.
          </p>
          <form
            className="staff-grant-form"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              void grant(
                String(data.get("email")),
                String(data.get("role")),
                true,
              );
            }}
          >
            <label>
              Staff email
              <input type="email" name="email" required autoComplete="off" />
            </label>
            <label>
              Role
              <select name="role" defaultValue="staff">
                <option value="staff">Staff</option>
                <option value="admin">Administrator</option>
              </select>
            </label>
            <button className="primary-button" disabled={busy}>
              Grant access
            </button>
          </form>
          <small>Use the email of an existing staff sign-in account.</small>
          <div className="staff-member-list">
            {members.map((member) => (
              <div className="staff-member" key={member.id}>
                <span>
                  <strong>{member.email}</strong>
                  {member.id === session.id && " (you)"}
                  <small>
                    {member.role === "admin" ? "Administrator" : "Staff"} ·{" "}
                    {member.enabled ? "Active" : "Disabled"}
                  </small>
                </span>
                <label className="sr-only" htmlFor={`role-${member.id}`}>
                  Role for {member.email}
                </label>
                <select
                  id={`role-${member.id}`}
                  value={member.role}
                  disabled={busy}
                  onChange={(event) =>
                    void grant(member.email, event.target.value, member.enabled)
                  }
                >
                  <option value="staff">Staff</option>
                  <option value="admin">Administrator</option>
                </select>
                <button
                  disabled={busy}
                  onClick={() =>
                    void grant(member.email, member.role, !member.enabled)
                  }
                >
                  {member.enabled ? "Disable access" : "Enable access"}
                </button>
              </div>
            ))}
          </div>
        </>
      )}
      <details>
        <summary>Change your password</summary>
        <form className="staff-password-form" onSubmit={changePassword}>
          <label>
            New password
            <input
              type="password"
              name="password"
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
              required
            />
          </label>
          <label>
            Confirm password
            <input
              type="password"
              name="confirm"
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
              required
            />
          </label>
          <button disabled={busy} className="primary-button">
            Update password
          </button>
        </form>
      </details>
    </section>
  );
}
