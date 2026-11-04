"use client";

import { staffFetch } from "@/lib/staff-fetch";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

type Provider = {
  state: string;
  error: string | null;
  checked_at: string | null;
};
type Status = {
  storage: string;
  providers: Record<string, Provider>;
  index: {
    pending_documents: number;
    failed_documents: number;
    indexed_documents: number;
  };
  failed_jobs: {
    document_id: string;
    title: string;
    error: string;
    attempts: number;
  }[];
  expiry_warnings: { id: string; title: string; expires_on: string }[];
};

function explanation(error: string | null) {
  if (!error) return "Ready";
  if (error.includes("access_denied"))
    return "Project access denied. Check the Gemini project in AI Studio.";
  if (error.includes("billing"))
    return "Gemini credits need attention in AI Studio.";
  if (error.includes("not_configured")) return "No API key configured.";
  if (error.includes("timeout") || error.includes("busy"))
    return "Provider temporarily unavailable. Source passages remain available.";
  return "Check the provider configuration and try again.";
}

export default function AdminStatus() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await staffFetch("/api/admin/system/status", { signal });
      if (!response.ok)
        throw new Error("Status is unavailable. Try refreshing.");
      const data: Status = await response.json();
      if (!signal?.aborted) {
        setStatus(data);
        setError("");
      }
    } catch (err) {
      if (!signal?.aborted) setError((err as Error).message);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = setInterval(() => void refresh(controller.signal), 30000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [refresh]);
  async function action(path: string, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await staffFetch(path, { method: "POST" });
      if (!response.ok)
        throw new Error("The operation could not finish. Please try again.");
      setNotice(message);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="operations-panel" aria-label="System status">
      <div className="operations-heading">
        <div>
          <div className="eyebrow">SERVICE & COLLECTION HEALTH</div>
          <h3>System status</h3>
        </div>
        <button disabled={busy} onClick={() => void refresh()}>
          <RefreshCw size={15} /> Refresh
        </button>
      </div>
      {error && (
        <p role="alert" className="error-message">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="success-message">
          {notice}
        </p>
      )}
      {status && (
        <>
          <div className="operations-grid">
            <div>
              <strong>Document storage</strong>
              <p>{status.storage}</p>
            </div>
            {Object.entries(status.providers).map(([name, provider]) => (
              <div key={name}>
                <strong>
                  {name === "generation" ? "Gemini answers" : "Semantic search"}
                </strong>
                <span className={`service-state ${provider.state}`}>
                  {provider.state.replaceAll("_", " ")}
                </span>
                <p>
                  {provider.state === "not_checked"
                    ? "No request observed since the last restart. Run a check to verify access."
                    : explanation(provider.error)}
                </p>
                {provider.checked_at && (
                  <small>
                    Checked {new Date(provider.checked_at).toLocaleTimeString()}
                  </small>
                )}
              </div>
            ))}
            <div>
              <strong>Search indexing</strong>
              <p>
                {status.index.indexed_documents} indexed ·{" "}
                {status.index.pending_documents} queued ·{" "}
                {status.index.failed_documents} failed
              </p>
            </div>
          </div>
          <div className="operations-actions">
            <button
              disabled={busy}
              onClick={() =>
                void action(
                  "/api/admin/system/check",
                  "Provider check complete.",
                )
              }
            >
              {busy ? "Working…" : "Check AI services"}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void action(
                  "/api/admin/search/reindex",
                  "Reindex queued. Progress refreshes every 30 seconds.",
                )
              }
            >
              Reindex collection
            </button>
          </div>
          {status.failed_jobs.length > 0 && (
            <div className="operations-notice">
              <strong>Indexing needs attention</strong>
              {status.failed_jobs.map((job) => (
                <div className="job-row" key={job.document_id}>
                  <span>
                    {job.title} · {job.error} · {job.attempts} attempts
                  </span>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void action(
                        `/api/admin/search/${job.document_id}/retry`,
                        "Retry queued.",
                      )
                    }
                  >
                    Retry
                  </button>
                </div>
              ))}
            </div>
          )}
          {status.expiry_warnings.length > 0 && (
            <div className="operations-notice">
              <strong>Expiry review · next 30 days and overdue</strong>
              <ul>
                {status.expiry_warnings.map((doc) => (
                  <li key={doc.id}>
                    {doc.title} — {doc.expires_on}
                  </li>
                ))}
              </ul>
              <p>
                Expired documents are excluded from student searches. Update
                their details below and review them before approving.
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
