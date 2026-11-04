"use client";

import { useEffect, useRef, useState } from "react";
import { answerSources } from "@/lib/answer-sources";
import StaffSignIn from "@/components/staff-sign-in";
import StaffAccounts from "@/components/staff-accounts";
import { staffFetch, staffJson, type StaffSession } from "@/lib/staff-fetch";
import DocumentReview from "@/components/document-review";
import AdminStatus from "@/components/admin-status";
import DocumentTools from "@/components/document-tools";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleHelp,
  Compass,
  ExternalLink,
  FileText,
  LoaderCircle,
  MapPin,
  Plus,
  Search,
  ShieldCheck,
  ThumbsDown,
  ThumbsUp,
  Upload,
  Users,
  X,
} from "lucide-react";

type Doc = {
  id: string;
  title: string;
  department: string;
  status: string;
  sample: boolean;
  expires_on: string | null;
  previous_id?: string | null;
  replacement_id?: string | null;
};
type Source = {
  id: number;
  title: string;
  page: number;
  excerpt: string;
  sample: boolean;
  url: string;
};
type Answer = {
  id: string;
  answer: string;
  sources: Source[];
  mode: string;
  fallback: boolean;
  sample: boolean;
};
type Message =
  { role: "user"; text: string } | { role: "assistant"; result: Answer };
type Department = { name: string; description: string; location: string };
type View = "chat" | "documents" | "departments" | "admin";

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await staffFetch(url, init);
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(
      typeof detail?.detail === "string"
        ? detail.detail
        : "Could not complete the request. Check that the backend is running.",
    );
  }
  return response.json();
}

const suggestions = [
  {
    category: "LIBRARY & LEARNING",
    title: "When is the library open?",
    question: "What are the library opening hours?",
  },
  {
    category: "ACADEMIC SUPPORT",
    title: "Get a bonafide certificate",
    question: "How do I apply for a bonafide certificate?",
  },
  {
    category: "CAMPUS LIFE",
    title: "Connect to campus Wi-Fi",
    question: "How can I connect to campus Wi-Fi?",
  },
  {
    category: "EXAMS & SCHEDULES",
    title: "Find my exam timetable",
    question: "Where can I find my examination timetable?",
  },
];

export default function Home() {
  const [view, setView] = useState<View>("chat");
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [online, setOnline] = useState(false);
  const [mode, setMode] = useState("extractive");
  const [selected, setSelected] = useState<Source | null>(null);
  const [staff, setStaff] = useState<StaffSession | null>(null);
  const [adminDocs, setAdminDocs] = useState<Doc[]>([]);
  const unlocked = staff !== null;
  const authGeneration = useRef(0);
  const signingOut = useRef(false);
  const [adminBusy, setAdminBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [adminDepartment, setAdminDepartment] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const filteredDocs = documents.filter(
    (doc) =>
      (!departmentFilter || doc.department === departmentFilter) &&
      `${doc.title} ${doc.department}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
  );
  const filteredAdminDocs = adminDocs.filter(
    (doc) =>
      (!adminDepartment || doc.department === adminDepartment) &&
      (!statusFilter || doc.status === statusFilter),
  );
  const [review, setReview] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const [feedback, setFeedback] = useState<Record<string, boolean>>({});
  const displayMessages = messages.map((message) => {
    if (message.role !== "assistant") return message;
    const displayed = answerSources(
      message.result.answer,
      message.result.sources,
    );
    return {
      ...message,
      result: {
        ...message.result,
        ...displayed,
        sample: displayed.sources.some((source) => source.sample),
      },
    };
  });
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  async function refresh() {
    try {
      const [health, docs, deps] = await Promise.all([
        api<{ mode: string }>("/api/health"),
        api<Doc[]>("/api/documents"),
        api<Department[]>("/api/departments"),
      ]);
      setMode(health.mode);
      setDocuments(docs);
      setDepartments(deps);
      setOnline(true);
    } catch {
      setOnline(false);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelected(null);
        setReview(null);
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  useEffect(() => {
    if (!selected && !review) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'button, a[href], input, select, textarea, [tabindex="0"]',
        ) || [],
      );
    focusable()[0]?.focus();
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trapFocus);
    return () => {
      document.removeEventListener("keydown", trapFocus);
      previous?.focus();
    };
  }, [selected?.url, selected?.id, review?.id]);

  async function ask(question: string) {
    if (busy || !question.trim()) return;
    setView("chat");
    setInput("");
    setError("");
    setBusy(true);
    setMessages((previous) => [
      ...previous,
      { role: "user", text: question.trim() },
    ]);
    try {
      const result = await api<Answer>("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      setMessages((previous) => [...previous, { role: "assistant", result }]);
      setOnline(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      composer.current?.focus();
    }
  }

  async function sendFeedback(answerId: string, helpful: boolean) {
    try {
      await api("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer_id: answerId, helpful }),
      });
      setFeedback((old) => ({ ...old, [answerId]: helpful }));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function signedIn(session: StaffSession) {
    authGeneration.current += 1;
    setStaff(session);
    setError("");
    await refreshAdmin();
  }

  async function signOut() {
    signingOut.current = true;
    authGeneration.current += 1;
    try {
      await staffJson("/api/auth/logout", { method: "POST" });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      authGeneration.current += 1;
      signingOut.current = false;
      setStaff(null);
      setAdminDocs([]);
      setReview(null);
    }
  }

  useEffect(() => {
    if (view !== "admin") return;
    let active = true;
    const ended = () => {
      authGeneration.current += 1;
      setStaff(null);
      setAdminDocs([]);
      setReview(null);
    };
    const restore = async () => {
      if (signingOut.current) return;
      const generation = authGeneration.current;
      try {
        const session = await staffJson<StaffSession>("/api/auth/session");
        if (active && generation === authGeneration.current) {
          setStaff(session);
          const docs = await api<Doc[]>("/api/admin/documents");
          if (active && generation === authGeneration.current)
            setAdminDocs(docs);
        }
      } catch {
        if (active && generation === authGeneration.current) ended();
      }
    };
    void restore();
    const timer = window.setInterval(() => void restore(), 60_000);
    window.addEventListener("staff-session-ended", ended);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("staff-session-ended", ended);
    };
  }, [view]);

  async function refreshAdmin() {
    setAdminDocs(await api<Doc[]>("/api/admin/documents", {}));
    await refresh();
  }

  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAdminBusy(true);
    setError("");
    setNotice("");
    const form = event.currentTarget;
    try {
      await api("/api/admin/documents", {
        method: "POST",

        body: new FormData(form),
      });
      form.reset();
      setNotice("Document uploaded. Approve it below to make it searchable.");
      setAdminDocs(await api<Doc[]>("/api/admin/documents", {}));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAdminBusy(false);
    }
  }

  async function changeStatus(id: string, action: "approve" | "archive") {
    setAdminBusy(true);
    setError("");
    try {
      await api(`/api/admin/documents/${id}/${action}`, {
        method: "POST",
      });
      setAdminDocs(await api<Doc[]>("/api/admin/documents", {}));
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAdminBusy(false);
    }
  }

  const resetQuestion = () => {
    setMessages([]);
    setView("chat");
    setError("");
    setInput("");
  };
  const nav = (id: View, label: string) => (
    <button
      aria-current={view === id ? "page" : undefined}
      className={`nav-item ${view === id ? "active" : ""}`}
      onClick={() => {
        setView(id);
        setError("");
        setNotice("");
      }}
    >
      {label}
    </button>
  );
  const questionForm = (
    <div className="composer-container">
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(input);
        }}
      >
        <label htmlFor="campus-question">
          {messages.length ? "Ask another question" : "Your question"}
        </label>
        <p id="question-hint" className="field-hint">
          Include your programme or department if it helps narrow things down.
        </p>
        <textarea
          id="campus-question"
          ref={composer}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void ask(input);
            }
          }}
          placeholder="For example, how do I apply for a bonafide certificate?"
          rows={3}
          maxLength={2000}
          aria-describedby="question-hint"
        />
        <div className="composer-actions">
          <button
            className="primary-button"
            disabled={busy || !input.trim()}
            type="submit"
          >
            {busy ? (
              <LoaderCircle size={17} className="spin" />
            ) : (
              <Search size={17} />
            )}
            {busy ? "Looking through documents…" : "Find an answer"}
          </button>
          <span className="keyboard-hint">
            Enter to send <span>↵</span>
          </span>
        </div>
      </form>
      <p className="composer-caption">
        <BookOpen size={14} />
        {mode === "gemini"
          ? "Answers include references to the documents used."
          : "Results show matching passages from the document collection."}
      </p>
    </div>
  );

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="site-header">
        <div className="utility-bar">
          <div className="site-width">
            <span>Student information & support</span>
            <span>Demonstration campus</span>
          </div>
        </div>
        <div className="masthead site-width">
          <a href="/" className="brand" aria-label="Campus Compass home">
            <span className="brand-mark" aria-hidden="true">
              c<span>c</span>
              <i />
            </span>
            <span>
              Campus Compass<small>A guide to your college</small>
            </span>
          </a>
          <button
            className="staff-link"
            onClick={() => {
              setView("admin");
              setError("");
            }}
          >
            Staff workspace <ArrowRight size={15} />
          </button>
        </div>
        <div className="navigation-bar">
          <nav className="site-width" aria-label="Main navigation">
            {nav("chat", "Ask a question")}
            {nav("documents", "Documents & guides")}
            {nav("departments", "Department directory")}
            <span
              className={`connection ${online ? "online" : ""}`}
              role="status"
            >
              <i />
              {online ? "Search available" : "Search unavailable"}
            </span>
          </nav>
        </div>
      </header>
      <main id="main-content" tabIndex={-1}>
        <div className="page-context site-width">
          <span>Student services</span>
          <ChevronRight size={12} />
          <span>
            {view === "chat"
              ? "Ask a question"
              : view === "documents"
                ? "Documents & guides"
                : view === "departments"
                  ? "Department directory"
                  : "Staff workspace"}
          </span>
        </div>
        {error && (
          <div className="error-banner site-width" role="alert">
            {error}
            <button aria-label="Dismiss error" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        {view === "chat" && (
          <div
            className={`chat-page site-width ${messages.length ? "has-messages" : ""}`}
          >
            <div className="chat-layout">
              <div className="chat-main">
                {!messages.length ? (
                  <header className="welcome">
                    <div className="eyebrow">THE STUDENT HELP DESK</div>
                    <h1>
                      What do you
                      <br />
                      need to know?
                    </h1>
                    <p className="intro">
                      Find your way through college life. Ask about academic
                      procedures, campus services or where to go for help.
                    </p>
                  </header>
                ) : (
                  <div className="discussion-heading">
                    <div>
                      <div className="eyebrow">THE STUDENT HELP DESK</div>
                      <h1>Your questions</h1>
                    </div>
                    <button className="text-button" onClick={resetQuestion}>
                      <Plus size={15} /> New question
                    </button>
                  </div>
                )}
                {messages.length > 0 && (
                  <div className="conversation" aria-live="polite">
                    {displayMessages.map((message, index) =>
                      message.role === "user" ? (
                        <div className="user-message" key={index}>
                          <div>{message.text}</div>
                          <span className="avatar">S</span>
                        </div>
                      ) : (
                        <article
                          className="answer-message"
                          key={message.result.id}
                        >
                          <span className="assistant-avatar">
                            <Compass size={20} />
                          </span>
                          <div className="answer-body">
                            <div className="answer-heading">
                              Compass{" "}
                              <span>
                                {message.result.mode === "gemini"
                                  ? "AI answer"
                                  : "Source excerpts"}
                              </span>
                            </div>
                            {message.result.sample && (
                              <div className="sample-notice">
                                Sample documents · verify with your college
                                before acting.
                              </div>
                            )}
                            <p className="answer-text">
                              {message.result.answer}
                            </p>
                            {message.result.sources.length > 0 && (
                              <div className="sources">
                                <div className="sources-label">
                                  <BookOpen size={14} /> SOURCES
                                </div>
                                {message.result.sources.map((source) => (
                                  <button
                                    key={source.id}
                                    className="source-chip"
                                    onClick={() => setSelected(source)}
                                  >
                                    <span>{source.id}</span>
                                    {source.title}
                                    <small>p. {source.page}</small>
                                    <ExternalLink size={12} />
                                  </button>
                                ))}
                              </div>
                            )}
                            {message.result.fallback && (
                              <button
                                className="text-button"
                                onClick={() => setView("departments")}
                              >
                                Find a department <ArrowRight size={14} />
                              </button>
                            )}
                            <div className="answer-feedback">
                              {message.result.id in feedback ? (
                                <span>
                                  <Check size={13} /> Feedback saved
                                </span>
                              ) : (
                                <>
                                  <span>Was this useful?</span>
                                  <button
                                    aria-label="Answer was useful"
                                    onClick={() =>
                                      void sendFeedback(message.result.id, true)
                                    }
                                  >
                                    <ThumbsUp size={14} />
                                  </button>
                                  <button
                                    aria-label="Answer was not useful"
                                    onClick={() =>
                                      void sendFeedback(
                                        message.result.id,
                                        false,
                                      )
                                    }
                                  >
                                    <ThumbsDown size={14} />
                                  </button>
                                </>
                              )}
                            </div>
                          </div>
                        </article>
                      ),
                    )}
                    {busy && (
                      <div className="thinking">
                        <LoaderCircle size={16} className="spin" /> Searching
                        campus documents…
                      </div>
                    )}
                    <div ref={bottom} />
                  </div>
                )}
                {questionForm}
                {!messages.length && (
                  <section
                    className="common-questions"
                    aria-labelledby="common-title"
                  >
                    <div className="section-title">
                      <h2 id="common-title">A few places to start</h2>
                      <span>Common questions</span>
                    </div>
                    <div className="suggestion-list">
                      {suggestions.map(
                        ({ title, question, category }, index) => (
                          <button
                            key={category}
                            disabled={busy}
                            onClick={() => void ask(question)}
                          >
                            <span className="row-number">0{index + 1}</span>
                            <span className="suggestion-title">{title}</span>
                            <ArrowRight size={17} />
                          </button>
                        ),
                      )}
                    </div>
                  </section>
                )}
              </div>
              <aside
                className="reference-rail"
                aria-label="Useful campus resources"
              >
                <section className="collection-panel">
                  <div className="rail-heading">
                    <BookOpen size={18} />
                    <span>THE REFERENCE SHELF</span>
                  </div>
                  <h2>
                    Go straight
                    <br />
                    to the source.
                  </h2>
                  <p>Browse the documents used to answer your questions.</p>
                  <div className="shelf-list">
                    {documents.slice(0, 3).map((doc, index) => (
                      <a
                        key={doc.id}
                        href={`/api/documents/${doc.id}/source`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <span className="shelf-number">0{index + 1}</span>
                        <span>
                          <strong>{doc.title}</strong>
                          <small>
                            {doc.department}
                            {doc.sample ? " · Sample" : ""}
                          </small>
                        </span>
                        <ExternalLink size={13} />
                      </a>
                    ))}
                    {!documents.length && (
                      <p className="rail-empty">
                        {online
                          ? "No documents have been published yet."
                          : "The document collection is currently unavailable."}
                      </p>
                    )}
                  </div>
                  <button
                    className="rail-link"
                    onClick={() => setView("documents")}
                  >
                    All documents{online && <span>({documents.length})</span>}
                    <ArrowRight size={15} />
                  </button>
                </section>
                <section className="human-help">
                  <div className="rail-heading">
                    <Users size={18} />
                    <span>ASK THE RIGHT PEOPLE</span>
                  </div>
                  <h2>
                    Some things need
                    <br />a conversation.
                  </h2>
                  <p>
                    For personal requests or a question the documents don’t
                    cover, start with the relevant office.
                  </p>
                  <button
                    className="rail-link"
                    onClick={() => setView("departments")}
                  >
                    Find a department <ArrowRight size={15} />
                  </button>
                </section>
                <details className="answer-explainer">
                  <summary>How these answers work</summary>
                  <p>
                    Questions are matched with approved documents. Open a source
                    reference to check the information in context. This
                    workspace contains sample material; confirm important dates
                    and policies with your college.
                  </p>
                </details>
              </aside>
            </div>
          </div>
        )}
        {view === "documents" && (
          <section className="content-page site-width">
            <div className="eyebrow">THE DOCUMENT COLLECTION</div>
            <h1>Documents &amp; guides</h1>
            <p className="intro">
              Read the college guides and procedures behind your answers.
            </p>
            <div className="search-field">
              <Search size={18} />
              <input
                placeholder="Search documents or departments…"
                aria-label="Search documents"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </div>
            <div className="collection-filters">
              <label>
                Department
                <select
                  value={departmentFilter}
                  onChange={(event) => setDepartmentFilter(event.target.value)}
                >
                  <option value="">All departments</option>
                  {Array.from(new Set(documents.map((doc) => doc.department)))
                    .sort()
                    .map((department) => (
                      <option key={department}>{department}</option>
                    ))}
                </select>
              </label>
            </div>
            <div className="document-grid">
              {filteredDocs.map((doc) => (
                <a
                  className="document-card"
                  href={`/api/documents/${doc.id}/source`}
                  target="_blank"
                  rel="noreferrer"
                  key={doc.id}
                >
                  <span className="document-icon">
                    <FileText size={24} />
                  </span>
                  <div className="card-meta">
                    {doc.department} {doc.sample && <span>Sample</span>}
                  </div>
                  <h3>{doc.title}</h3>
                  <div className="document-footer">
                    <span>
                      <ShieldCheck size={13} /> Approved
                    </span>
                    <ExternalLink size={15} />
                  </div>
                </a>
              ))}
            </div>
            {!documents.length && (
              <div className="empty-state">
                <BookOpen size={32} />
                <h3>No documents available</h3>
                <p>
                  {online
                    ? "Upload and approve a document in Manage knowledge."
                    : "The document collection is unavailable. Please try again shortly."}
                </p>
              </div>
            )}
            {documents.length > 0 && !filteredDocs.length && (
              <p>No documents match your filters.</p>
            )}
          </section>
        )}

        {view === "departments" && (
          <section className="content-page site-width">
            <div className="eyebrow">CAMPUS CONTACTS</div>
            <h1>Department directory</h1>
            <p className="intro">
              Find the office that can help with your request.
            </p>
            <div className="sample-banner">
              <CircleHelp size={18} /> These are sample departments and
              locations. Your college’s verified contacts will replace them.
            </div>
            <div className="department-list">
              {departments.map((department, index) => (
                <article key={department.name}>
                  <span
                    className={`suggestion-icon ${["green", "peach", "blue"][index % 3]}`}
                  >
                    <Users size={22} />
                  </span>
                  <div>
                    <h3>{department.name}</h3>
                    <p>{department.description}</p>
                    <small>
                      <MapPin size={13} />
                      {department.location}
                    </small>
                  </div>
                </article>
              ))}
            </div>
            {!departments.length && (
              <div className="empty-state">
                <Users size={32} />
                <h3>Directory unavailable</h3>
                <p>The directory is unavailable. Please try again shortly.</p>
              </div>
            )}
          </section>
        )}

        {view === "admin" && (
          <section className="content-page site-width">
            <div className="eyebrow">STAFF WORKSPACE</div>
            <h1>Manage documents</h1>
            <p className="intro">
              Upload documents, review them, then make them available to
              students.
            </p>
            {!unlocked ? (
              <StaffSignIn onSignedIn={signedIn} />
            ) : (
              <>
                <div className="admin-session">
                  <span>
                    <ShieldCheck size={16} /> {staff?.email} ·{" "}
                    {staff?.role === "admin" ? "Administrator" : "Staff"}
                  </span>
                  <button onClick={() => void signOut()}>Sign out</button>
                </div>
                <AdminStatus />
                {staff && <StaffAccounts session={staff} />}
                <form className="upload-form" onSubmit={upload}>
                  <h3>
                    <Upload size={19} /> Add a document
                  </h3>
                  <div className="form-grid">
                    <label>
                      Document title
                      <input
                        name="title"
                        required
                        minLength={2}
                        maxLength={200}
                        placeholder="e.g. Academic calendar 2026–27"
                      />
                    </label>
                    <label>
                      Department
                      <select name="department">
                        <option>Academic Office</option>
                        <option>Library</option>
                        <option>Student Services</option>
                      </select>
                    </label>
                    <label>
                      Expires on (optional)
                      <input type="date" name="expires_on" />
                    </label>
                    <label>
                      Source file
                      <input
                        name="file"
                        type="file"
                        accept=".pdf,.docx,.txt,.md"
                        required
                      />
                    </label>
                  </div>
                  <p className="muted">
                    PDF, DOCX, TXT or Markdown · up to 10 MB · scanned files are
                    processed with local OCR when needed.
                  </p>
                  <button className="primary-button" disabled={adminBusy}>
                    {adminBusy ? "Processing…" : "Upload for review"}
                    <Upload size={15} />
                  </button>
                </form>
                {notice && (
                  <p className="success-message" role="status">
                    <Check size={15} />
                    {notice}
                  </p>
                )}
                <h3 className="section-heading">
                  Document collection <span>{adminDocs.length}</span>
                </h3>
                <div className="collection-filters">
                  <label>
                    Department
                    <select
                      value={adminDepartment}
                      onChange={(event) =>
                        setAdminDepartment(event.target.value)
                      }
                    >
                      <option value="">All departments</option>
                      {Array.from(
                        new Set(adminDocs.map((doc) => doc.department)),
                      )
                        .sort()
                        .map((department) => (
                          <option key={department}>{department}</option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Status
                    <select
                      value={statusFilter}
                      onChange={(event) => setStatusFilter(event.target.value)}
                    >
                      <option value="">All statuses</option>
                      <option value="pending">Pending</option>
                      <option value="approved">Approved</option>
                      <option value="archived">Archived</option>
                    </select>
                  </label>
                </div>
                <div className="admin-docs">
                  {filteredAdminDocs.map((doc) => (
                    <div className="admin-record" key={doc.id}>
                      <article>
                        <FileText size={20} />
                        <div>
                          <strong>{doc.title}</strong>
                          <small>
                            {doc.department}
                            {doc.sample ? " · Sample" : ""}
                            {doc.expires_on
                              ? ` · Expires ${doc.expires_on}`
                              : ""}
                          </small>
                        </div>
                        <span className={`status-pill ${doc.status}`}>
                          {doc.status}
                        </span>
                        <button
                          disabled={adminBusy}
                          onClick={() =>
                            setReview({ id: doc.id, title: doc.title })
                          }
                        >
                          Review
                        </button>
                        {doc.status !== "approved" && !doc.replacement_id && (
                          <button
                            disabled={adminBusy}
                            onClick={() => void changeStatus(doc.id, "approve")}
                          >
                            Approve
                          </button>
                        )}
                        {doc.status !== "archived" && (
                          <button
                            disabled={adminBusy}
                            onClick={() => void changeStatus(doc.id, "archive")}
                          >
                            Archive
                          </button>
                        )}
                      </article>
                      <DocumentTools doc={doc} onChanged={refreshAdmin} />
                    </div>
                  ))}
                  {!filteredAdminDocs.length && (
                    <p>No documents match these filters.</p>
                  )}
                </div>
              </>
            )}
          </section>
        )}
      </main>
      <footer className="site-footer site-width">
        <div>
          <span className="footer-name">Campus Compass</span>
          <p>College information, in one place.</p>
        </div>
        <div className="footer-note">
          <span className="demo-label">Demonstration</span>
          <p>
            Sample information. Confirm dates and policies with your college.
          </p>
        </div>
        <button
          onClick={() => {
            setView("admin");
            setError("");
          }}
        >
          Staff access <ArrowRight size={14} />
        </button>
      </footer>
      {review && (
        <DocumentReview
          documentId={review.id}
          title={review.title}
          onClose={() => setReview(null)}
          onChanged={async () => {
            setAdminDocs(await api<Doc[]>("/api/admin/documents", {}));
            await refresh();
          }}
        />
      )}
      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <section
            role="dialog"
            aria-modal="true"
            aria-label="Source details"
            className="source-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              className="modal-close"
              aria-label="Close source"
              onClick={() => setSelected(null)}
            >
              <X size={20} />
            </button>
            <div className="eyebrow">
              SOURCE {selected.id} · PAGE {selected.page}
            </div>
            <h2>{selected.title}</h2>
            {selected.sample && (
              <div className="sample-notice">
                Demonstration content — not official college information.
              </div>
            )}
            <blockquote>{selected.excerpt}</blockquote>
            <a
              className="primary-button"
              href={selected.url}
              target="_blank"
              rel="noreferrer"
            >
              Open source document <ExternalLink size={16} />
            </a>
          </section>
        </div>
      )}
    </div>
  );
}
