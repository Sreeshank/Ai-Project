"use client";

import { staffFetch } from "@/lib/staff-fetch";

import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  Save,
  X,
} from "lucide-react";

type Page = {
  page: number;
  text: string;
  method: string;
  warnings: string[];
  confidence: number | null;
  reviewed: boolean;
  has_image: boolean;
};
type Props = {
  documentId: string;
  title: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
};
async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await staffFetch(path, {
    ...init,
    headers: init?.headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : "The request could not be completed.",
    );
  }
  return response.json();
}

export default function DocumentReview({
  documentId,
  title,
  onClose,
  onChanged,
}: Props) {
  const [pages, setPages] = useState<Page[]>([]);
  const [pageIndex, setPageIndex] = useState(0);
  const [image, setImage] = useState("");
  const [imageError, setImageError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [saved, setSaved] = useState(false);
  const [imageLoading, setImageLoading] = useState(false);
  const page = pages[pageIndex];

  async function loadPages() {
    const next = await jsonRequest<Page[]>(
      `/api/admin/documents/${documentId}/preview`,
    );
    setPages(next);
    setPageIndex(0);
    setDirty(false);
    setConfirmed(false);
  }
  useEffect(() => {
    let active = true;
    jsonRequest<Page[]>(`/api/admin/documents/${documentId}/preview`)
      .then((next) => {
        if (active) setPages(next);
      })
      .catch((err) => {
        if (active) setError(err.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [documentId]);

  useEffect(() => {
    setImage("");
    setImageError("");
    if (!page?.has_image) return;
    const controller = new AbortController();
    let url = "";
    setImageLoading(true);
    staffFetch(`/api/admin/documents/${documentId}/pages/${page.page}/image`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok)
          throw new Error("The original page could not be loaded.");
        return response.blob();
      })
      .then((blob) => {
        if (!controller.signal.aborted) {
          url = URL.createObjectURL(blob);
          setImage(url);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) setImageError(err.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setImageLoading(false);
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [documentId, page?.page, page?.has_image]);

  async function reprocess() {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await jsonRequest(`/api/admin/documents/${documentId}/reprocess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force_ocr: true }),
      });
      await loadPages();
      await onChanged();
      setEditing(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      await jsonRequest(`/api/admin/documents/${documentId}/review`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pages: pages.map(({ page, text }) => ({ page, text })),
        }),
      });
      setDirty(false);
      setSaved(true);
      setPages((previous) =>
        previous.map((item) => ({ ...item, reviewed: true })),
      );
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay review-overlay">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-title"
        className="document-review"
      >
        <header className="review-header">
          <div>
            <div className="eyebrow">DOCUMENT REVIEW</div>
            <h2 id="review-title">{title}</h2>
            <p>
              Compare the original with the extracted text before publishing.
            </p>
          </div>
          <button
            className="review-close"
            onClick={onClose}
            aria-label="Close review"
          >
            <X size={21} />
          </button>
        </header>
        {error && (
          <div className="review-alert" role="alert">
            <AlertTriangle size={16} />
            {error}
          </div>
        )}
        {loading ? (
          <div className="review-loading">
            <LoaderCircle className="spin" size={20} /> Loading document pages…
          </div>
        ) : !page ? (
          <p className="review-loading">
            There are no extracted pages to review.
          </p>
        ) : (
          <>
            <div className="review-toolbar">
              <label>
                Page{" "}
                <select
                  value={pageIndex}
                  disabled={busy}
                  onChange={(event) => setPageIndex(Number(event.target.value))}
                >
                  {pages.map((item, index) => (
                    <option key={item.page} value={index}>
                      {item.page} of {pages.length}
                    </option>
                  ))}
                </select>
              </label>
              <span className="extraction-badge">
                {page.method === "ocr"
                  ? "Local OCR"
                  : page.method === "legacy"
                    ? "Previous extraction"
                    : page.method === "text-fallback"
                      ? "Text fallback"
                      : "Text extraction"}
              </span>
              {page.reviewed && (
                <span className="reviewed-label">
                  <Check size={12} /> Reviewed
                </span>
              )}
              {dirty && <span className="unsaved-label">Unsaved changes</span>}
              {page.has_image && (
                <button
                  disabled={busy || dirty}
                  onClick={() => void reprocess()}
                >
                  <RefreshCw size={14} className={busy ? "spin" : ""} />
                  {busy ? "Processing…" : "Run OCR again"}
                </button>
              )}
            </div>
            {page.warnings.length > 0 && !page.reviewed && (
              <div className="extraction-warning">
                <AlertTriangle size={17} />
                <div>
                  <strong>Check the extraction</strong>
                  <ul>
                    {page.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
            <div
              className={`review-columns ${page.has_image ? "" : "text-only"}`}
            >
              {page.has_image && (
                <div className="original-panel">
                  <div className="review-panel-heading">
                    <h3>Original page</h3>
                    {image && (
                      <a href={image} target="_blank" rel="noreferrer">
                        Open full size <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                  <div className="page-image-container">
                    {imageLoading && (
                      <span className="review-loading">
                        <LoaderCircle className="spin" size={18} /> Loading
                        original…
                      </span>
                    )}
                    {imageError && <p role="alert">{imageError}</p>}
                    {image && (
                      <img
                        src={image}
                        alt={`Original page ${page.page} of ${title}`}
                      />
                    )}
                  </div>
                </div>
              )}
              <div className="extracted-panel">
                <div className="review-panel-heading">
                  <h3>Extracted text</h3>
                  <div className="review-tabs">
                    <button
                      aria-pressed={!editing}
                      onClick={() => setEditing(false)}
                    >
                      Read
                    </button>
                    <button
                      aria-pressed={editing}
                      onClick={() => setEditing(true)}
                    >
                      Edit
                    </button>
                  </div>
                </div>
                {editing ? (
                  <>
                    <p className="edit-hint">
                      Correct recognition errors here. Headings, lists and
                      Markdown tables are supported.
                    </p>
                    <textarea
                      className="review-editor"
                      aria-label={`Edit extracted text for page ${page.page}`}
                      value={page.text}
                      disabled={busy}
                      onChange={(event) => {
                        const text = event.target.value;
                        setPages((previous) =>
                          previous.map((item, index) =>
                            index === pageIndex ? { ...item, text } : item,
                          ),
                        );
                        setDirty(true);
                        setSaved(false);
                        setConfirmed(false);
                      }}
                      maxLength={100000}
                    />
                  </>
                ) : (
                  <div className="extracted-document">
                    <Markdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        a: ({ children, href }) => (
                          <a href={href} target="_blank" rel="noreferrer">
                            {children}
                          </a>
                        ),
                      }}
                    >
                      {page.text}
                    </Markdown>
                  </div>
                )}
              </div>
            </div>
            <footer className="review-footer">
              <div>
                <label className="review-confirmation">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    disabled={busy}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />{" "}
                  I checked all pages against the original, including dates and
                  amounts.
                </label>
                <p>
                  Saving updates search results and returns this document to
                  pending approval. Archived documents stay archived.
                </p>
                {saved && (
                  <span className="review-saved" role="status">
                    <Check size={14} /> Review saved. You can now approve the
                    document from the collection.
                  </span>
                )}
              </div>
              <button
                className="primary-button"
                disabled={busy || !confirmed || saved}
                onClick={() => void save()}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <Save size={16} />
                )}
                Save review
              </button>
            </footer>
          </>
        )}
      </section>
    </div>
  );
}
