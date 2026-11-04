"use client";

import { staffFetch } from "@/lib/staff-fetch";

import { useState } from "react";

type Document = {
  id: string;
  title: string;
  department: string;
  expires_on: string | null;
  replacement_id?: string | null;
};
type History = {
  versions: {
    id: string;
    title: string;
    filename: string;
    status: string;
    version: number;
  }[];
  revisions: { id: string; created_at: string; reason: string }[];
};

export default function DocumentTools({
  doc,
  onChanged,
}: {
  doc: Document;
  onChanged: () => Promise<void>;
}) {
  const [view, setView] = useState<"history" | "replace" | "details" | null>(
    null,
  );
  const [history, setHistory] = useState<History | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const headers = {};
  async function getHistory() {
    setView("history");
    setBusy(true);
    setError("");
    try {
      const response = await staffFetch(
        `/api/admin/documents/${doc.id}/history`,
        { headers },
      );
      if (!response.ok) throw new Error("History could not be loaded.");
      setHistory(await response.json());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const replacing = view === "replace";
    try {
      const response = await staffFetch(
        `/api/admin/documents/${doc.id}${replacing ? "/replace" : ""}`,
        {
          method: replacing ? "POST" : "PATCH",
          headers: replacing
            ? headers
            : { ...headers, "Content-Type": "application/json" },
          body: replacing ? form : JSON.stringify(Object.fromEntries(form)),
        },
      );
      if (!response.ok) {
        const data = await response.json();
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "The change could not be saved.",
        );
      }
      setView(null);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download(id: string) {
    setError("");
    try {
      const response = await staffFetch(`/api/admin/documents/${id}/original`, {
        headers,
      });
      if (!response.ok) throw new Error("Original file is unavailable.");
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download =
        history?.versions.find((v) => v.id === id)?.filename || "original";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  async function revision(id: string) {
    setBusy(true);
    setError("");
    try {
      const response = await staffFetch(
        `/api/admin/documents/${doc.id}/revisions/${id}`,
        { headers },
      );
      if (!response.ok) throw new Error("Revision could not be loaded.");
      const data: { pages: { page: number; text: string }[] } =
        await response.json();
      setText(data.pages.map((p) => `PAGE ${p.page}\n${p.text}`).join("\n\n"));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="document-tools">
      <div className="tool-buttons">
        <button disabled={busy} onClick={() => void getHistory()}>
          Versions &amp; history
        </button>
        <button
          disabled={busy || !!doc.replacement_id}
          onClick={() => {
            setView("replace");
            setError("");
          }}
        >
          Replace file
        </button>
        <button
          disabled={busy || !!doc.replacement_id}
          onClick={() => {
            setView("details");
            setError("");
          }}
        >
          Edit details
        </button>
        {doc.replacement_id && <small>A newer version exists.</small>}
      </div>
      {error && (
        <p role="alert" className="error-message">
          {error}
        </p>
      )}
      {view && (
        <section
          className="document-tool-detail"
          aria-label={`${doc.title} ${view}`}
        >
          <button
            className="close-detail"
            onClick={() => {
              setView(null);
              setText("");
            }}
          >
            Close
          </button>
          {view === "history" && (
            <>
              <h4>Version history</h4>
              {busy && <p role="status">Loading…</p>}
              {history?.versions.map((version) => (
                <div className="version-row" key={version.id}>
                  <span>
                    <strong>Version {version.version}</strong> ·{" "}
                    {version.filename} · {version.status}
                  </span>
                  <button onClick={() => void download(version.id)}>
                    Download original
                  </button>
                </div>
              ))}
              <h4>Earlier extracted text for this file</h4>
              {history?.revisions.length === 0 && (
                <p>No earlier text revisions.</p>
              )}
              {history?.revisions.map((item) => (
                <div className="version-row" key={item.id}>
                  <span>
                    {new Date(item.created_at).toLocaleString()} · {item.reason}
                  </span>
                  <button
                    disabled={busy}
                    onClick={() => void revision(item.id)}
                  >
                    View text
                  </button>
                </div>
              ))}
              {text && <pre className="revision-text">{text}</pre>}
            </>
          )}
          {view === "replace" && (
            <form onSubmit={save}>
              <h4>Upload a replacement</h4>
              <p>
                The new file needs review and approval. The currently approved
                version stays available until then.
              </p>
              <label>
                Replacement file
                <input
                  type="file"
                  name="file"
                  accept=".pdf,.docx,.txt,.md"
                  required
                />
              </label>
              <button disabled={busy} className="primary-button">
                {busy ? "Processing…" : "Upload replacement for review"}
              </button>
            </form>
          )}
          {view === "details" && (
            <form onSubmit={save}>
              <h4>Document details</h4>
              <label>
                Title
                <input
                  name="title"
                  defaultValue={doc.title}
                  minLength={2}
                  maxLength={200}
                  required
                />
              </label>
              <label>
                Department
                <input
                  name="department"
                  defaultValue={doc.department}
                  maxLength={100}
                  required
                />
              </label>
              <label>
                Expiry date
                <input
                  name="expires_on"
                  type="date"
                  defaultValue={doc.expires_on || ""}
                />
              </label>
              <p>
                Saving returns an active document to pending review. Archived
                documents stay archived.
              </p>
              <button disabled={busy} className="primary-button">
                {busy ? "Saving…" : "Save details for review"}
              </button>
            </form>
          )}
        </section>
      )}
    </div>
  );
}
