
"use client";

import { useEffect, useState } from "react";
import {
  analyzeRepository,
  startRepair,
  getRepairStatus,
  getRepairHistory,
  submitPatchReview,
  type AnalyzeResponse,
  type RepairResponse,
  type RepairJob,
} from "@/lib/api";

type IconName =
  | "grid"
  | "git"
  | "activity"
  | "clock"
  | "settings"
  | "sun"
  | "moon"
  | "github"
  | "arrow"
  | "check"
  | "alert"
  | "code"
  | "plus"
  | "terminal";

function Icon({
  name,
  size = 18,
}: {
  name: IconName;
  size?: number;
}) {
  const paths: Record<IconName, React.ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    git: <><circle cx="6" cy="4" r="2" /><circle cx="18" cy="7" r="2" /><circle cx="6" cy="20" r="2" /><path d="M6 6v12M18 9a5 5 0 0 1-5 5H6" /></>,
    activity: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.7-1.4-2.4 1.4-1.1a8 8 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.7-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.7 1.4 2.4-1.4 1.1a8 8 0 0 1 0 2Z" /></>,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" /></>,
    moon: <path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z" />,
    github: <><path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 6v-3.9a3.4 3.4 0 0 0-.9-2.7c3-.3 6.2-1.5 6.2-6.7a5.2 5.2 0 0 0-1.4-3.6 4.8 4.8 0 0 0-.1-3.6s-1.2-.4-3.8 1.4a13 13 0 0 0-7 0C5.4 1.1 4.2 1.5 4.2 1.5a4.8 4.8 0 0 0-.1 3.6 5.2 5.2 0 0 0-1.4 3.6c0 5.2 3.2 6.4 6.2 6.7A3.4 3.4 0 0 0 8 18.1V22" /></>,
    arrow: <><path d="M5 12h14M12 5l7 7-7 7" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    alert: <><path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4m0 3h.01" /></>,
    code: <><path d="m8 8-4 4 4 4m8-8 4 4-4 4m-2-11-4 14" /></>,
    plus: <path d="M12 5v14m-7-7h14" />,
    terminal: <><path d="m4 17 6-6-6-6m8 12h8" /></>,
  };

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}


function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function formatStatus(status: string): string {
  return status.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function Home() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [themeLoaded, setThemeLoaded] = useState(false);
  const [repository, setRepository] = useState("");
  const [notice, setNotice] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
 const [analysisResult, setAnalysisResult] = useState<AnalyzeResponse | null>(null);
 const [isStartingRepair, setIsStartingRepair] = useState(false);
const [repairJob, setRepairJob] = useState<RepairResponse | null>(null);
const [repairStatus, setRepairStatus] = useState<RepairJob | null>(null);
const [reviewFeedback, setReviewFeedback] = useState("");
const [isSubmittingReview, setIsSubmittingReview] = useState(false);

  useEffect(() => {
  const frame = window.requestAnimationFrame(() => {
    const savedTheme = localStorage.getItem("asea-theme");

    if (savedTheme === "light" || savedTheme === "dark") {
      setTheme(savedTheme);
    }

    setThemeLoaded(true);
  });

  return () => window.cancelAnimationFrame(frame);
}, []);

useEffect(() => {
  if (!themeLoaded) {
    return;
  }

  document.documentElement.dataset.theme = theme;
  localStorage.setItem("asea-theme", theme);
}, [theme, themeLoaded]);

useEffect(() => {
  let cancelled = false;

  async function loadRepairHistory() {
    try {
      const history = await getRepairHistory(20, 0);
      if (cancelled) return;
      setRepairHistory(history.items);
      setHistoryTotal(history.total);
    } catch {
      // Keep the dashboard usable if history is temporarily unavailable.
    }
  }

  void loadRepairHistory();
  const timer = setInterval(loadRepairHistory, 5000);

  return () => {
    cancelled = true;
    clearInterval(timer);
  };
}, [repairJob?.job_id, repairStatus?.status]);

useEffect(() => {
  const jobId = repairJob?.job_id;

  if (!jobId) return;

  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function pollRepairStatus() {
    try {
      const status = await getRepairStatus(jobId!);

      if (cancelled) return;

      setRepairStatus(status);

      if (status.status === "completed" || status.status === "failed") {
        return;
      }

      timer = setTimeout(pollRepairStatus, 2000);
    } catch (error) {
      if (cancelled) return;

      setNotice(
        error instanceof Error
          ? error.message
          : "Unable to fetch repair status.",
      );

      timer = setTimeout(pollRepairStatus, 3000);
    }
  }

  void pollRepairStatus();

  return () => {
    cancelled = true;

    if (timer) {
      clearTimeout(timer);
    }
  };
}, [repairJob?.job_id]);

  
async function startAnalysis() {
  const repoUrl = repository.trim();

  if (!repoUrl) {
    setNotice("Please enter a GitHub repository URL.");
    return;
  }

  if (!/^https:\/\/github\.com\/[^/]+\/[^/]+\/?$/.test(repoUrl)) {
    setNotice("Please enter a valid GitHub repository URL.");
    return;
  }

  setIsAnalyzing(true);
  setNotice("Connecting to ASEA backend...");
  setAnalysisResult(null);

  try {
    const result = await analyzeRepository(repoUrl);

    setAnalysisResult(result);
    setNotice(result.message || "Repository analysis completed successfully.");
  } catch (error) {
    setNotice(
      error instanceof Error
        ? error.message
        : "An unexpected error occurred during analysis.",
    );
  } finally {
    setIsAnalyzing(false);
  }
}
async function handleStartRepair() {
  setIsStartingRepair(true);
  setNotice("Submitting autonomous repair job...");

  try {
    const result = await startRepair(repository.trim());

    setRepairJob(result);
    setNotice(`Repair job submitted successfully. Job ID: ${result.job_id}`);
  } catch (error) {
    setNotice(
      error instanceof Error
        ? error.message
        : "Failed to start repair.",
    );
  } finally {
    setIsStartingRepair(false);
  }
}
async function handlePatchReview(decision: "approved" | "rejected") {
  if (!repairJob?.job_id) {
    setNotice("No active repair job found.");
    return;
  }

  if (decision === "rejected" && !reviewFeedback.trim()) {
    setNotice("Please provide feedback before rejecting the patch.");
    return;
  }

  setIsSubmittingReview(true);
  setNotice(`Submitting patch ${decision} decision...`);

  try {
    await submitPatchReview(repairJob.job_id, {
      decision,
      feedback: reviewFeedback.trim(),
    });

    setReviewFeedback("");

    setNotice(
      decision === "approved"
        ? "Patch approved. ASEA is continuing the repair."
        : "Patch rejected. ASEA will use your feedback.",
    );
  } catch (error) {
    setNotice(
      error instanceof Error
        ? error.message
        : "Failed to submit patch review.",
    );
  } finally {
    setIsSubmittingReview(false);
  }
}


  const analysis = analysisResult?.analysis;
  const reviewRequest = readRecord(repairStatus?.review_request);
  const reviewBug = readRecord(reviewRequest?.bug);
  const repairResult = readRecord(repairStatus?.result);
  const auditLog = Array.isArray(repairResult?.audit_log)
    ? repairResult.audit_log.map(readRecord).filter(
        (entry): entry is Record<string, unknown> => entry !== null,
      )
    : [];
  const bugsDetected = reviewBug ? 1 : 0;
  const bugsRepaired = auditLog.filter((entry) => entry.event === "bug_repaired").length;
  const pendingReviews = repairStatus?.status === "waiting_for_review" ? 1 : 0;
  const currentJobs = repairHistory.map((job) => ({
    key: job.job_id,
    name: job.repository.split(/[\\/]/).filter(Boolean).pop() || job.repository,
    detail: job.message || job.error || "Repair workflow",
    status: formatStatus(job.status),
    color: job.status === "completed"
      ? "green"
      : job.status === "failed"
        ? "red"
        : "yellow",
    time: job.created_at
      ? new Date(job.created_at).toLocaleString()
      : "Recently",
  }));


  return (
    <main className="dashboard">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo"><Icon name="code" size={25} /></div>
          <div>
            <div className="brand-name">ASEA<span>.</span></div>
            <div className="brand-caption">AUTONOMOUS ENGINEERING</div>
          </div>
        </div>

        <div className="sidebar-label">WORKSPACE</div>
        <nav className="navigation">
          <a className="nav-item active" href="#dashboard">
            <Icon name="grid" /> Dashboard
          </a>
          <a className="nav-item" href="#repositories">
            <Icon name="git" /> Repositories
          </a>
          <a className="nav-item" href="#activity">
            <Icon name="activity" /> Agent Activity
          </a>
          <a className="nav-item" href="#history">
            <Icon name="clock" /> Repair History
          </a>
        </nav>

        <div className="sidebar-label integrations-label">INTEGRATIONS</div>
        <a className="nav-item" href="#github">
          <Icon name="github" /> GitHub
          <span className="connected-dot" />
        </a>

        <div className="sidebar-bottom">
          <div className="engine-card">
            <div className="engine-heading">
              <span className="engine-dot" /> ENGINE STATUS
            </div>
            <div className="engine-status">System ready</div>
            <div className="engine-description">ASEA engine is ready to analyze repositories.</div>
          </div>
          <a className="nav-item" href="#settings">
            <Icon name="settings" /> Settings
          </a>
          <div className="profile">
            <div className="avatar">A</div>
            <div className="profile-info">
              <strong>ASEA Workspace</strong>
              <span>Developer account</span>
            </div>
            <span className="profile-menu">···</span>
          </div>
        </div>
      </aside>

      <section className="main-content" id="dashboard">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> <strong>Dashboard</strong></div>
          <div className="topbar-actions">
            <span className="system-indicator"><span /> Backend-connected data</span>
            <button
  className="analyze-button"
  onClick={startAnalysis}
  disabled={isAnalyzing || isStartingRepair}
>
  {isAnalyzing ? "Analyzing..." : "Analyze repository"}
  {!isAnalyzing && <Icon name="arrow" size={17} />}
</button>

<button
  className="repair-button"
  onClick={handleStartRepair}
  disabled={isAnalyzing || isStartingRepair || !analysisResult}
>
  {isStartingRepair ? "Starting repair..." : "Start autonomous repair"}
</button>
<button
  className="theme-toggle"
  onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
  aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
  title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
>
  <Icon name={theme === "dark" ? "sun" : "moon"} size={19} />
</button>
            <div className="avatar top-avatar">A</div>
          </div>
        </header>

        <div className="content-area">
          <div className="welcome-row">
            <div>
              <div className="eyebrow">AUTONOMOUS SOFTWARE ENGINEERING</div>
              <h1>Engineering, <span>on autopilot.</span></h1>
              <p className="welcome-description">
                Analyze repositories, detect bugs, and generate validated fixes with your AI engineering agent.
              </p>
            </div>
            <div className="version-tag"> <span /> ASEA v1.0</div>
          </div>

          <section className="repository-panel" id="repositories">
            <div className="panel-top">
              <div>
                <div className="panel-kicker">GET STARTED</div>
                <h2>Analyze a repository</h2>
                <p>Connect a GitHub repository to start autonomous code analysis.</p>
              </div>
              <div className="github-mark"><Icon name="github" size={27} /></div>
            </div>
            <label className="input-label" htmlFor="repo-url">GITHUB REPOSITORY URL</label>
            <div className="repository-input-row">
              <div className="input-wrapper">
                <Icon name="git" size={19} />
                <input
                  id="repo-url"
                  type="url"
                  placeholder="https://github.com/username/repository"
                  value={repository}
                  onChange={(event) => {
                    setRepository(event.target.value);
                    setNotice("");
                  }}
                />
              </div>
              <button
  className="analyze-button"
  onClick={startAnalysis}
  disabled={isAnalyzing}
>
  {isAnalyzing ? "Analyzing..." : "Analyze repository"}
  {!isAnalyzing && <Icon name="arrow" size={17} />}
</button>
            </div>
            {notice && (
  <div className="input-notice" role="status">
    {notice}
  </div>
)}

{analysisResult && (
  <div className="analysis-result">
    <strong>Repository analysis completed</strong>
    <pre>
      {JSON.stringify(analysisResult.analysis, null, 2)}
    </pre>
  </div>
)}
{repairJob && (
  <div className="analysis-result">
    <strong>Autonomous Repair Job</strong>

    <p>
      <b>Job ID:</b> {repairJob.job_id}
    </p>

    <p>
      <b>Status:</b>{" "}
      {repairStatus?.status || repairJob.status}
    </p>

    <p>
      <b>Message:</b>{" "}
      {repairStatus?.message || repairJob.message}
    </p>

    {repairStatus?.error && (
      <p>
        <b>Error:</b> {repairStatus.error}
      </p>
    )}

    {repairStatus?.review_request && repairStatus.status === "waiting_for_review" && (
  <>
    <strong>Patch Review Required</strong>

    <pre>
      {JSON.stringify(
        repairStatus.review_request,
        null,
        2
      )}
    </pre>

    <textarea
      className="review-feedback"
      placeholder="Enter feedback (required when rejecting a patch)..."
      value={reviewFeedback}
      onChange={(event) => setReviewFeedback(event.target.value)}
      rows={3}
    />

    <div className="review-actions">
      <button
        className="review-approve"
        onClick={() => handlePatchReview("approved")}
        disabled={isSubmittingReview}
      >
        {isSubmittingReview ? "Submitting..." : "Approve Patch"}
      </button>

      <button
        className="review-reject"
        onClick={() => handlePatchReview("rejected")}
        disabled={isSubmittingReview || !reviewFeedback.trim()}
      >
        {isSubmittingReview ? "Submitting..." : "Reject Patch"}
      </button>
    </div>
  </>
)}

    {repairStatus?.result && (
      <>
        <strong>Repair Result</strong>
        <pre>
          {JSON.stringify(
            repairStatus.result,
            null,
            2
          )}
        </pre>
      </>
    )}
  </div>
)}

            <div className="input-footnote">
              <Icon name="check" size={14} /> Your code is analyzed in an isolated environment.
              <span>Human approval required before publishing fixes.</span>
            </div>
          </section>

          <div className="section-heading">
            <div>
              <h2>Workspace overview</h2>
              <p>A snapshot of your autonomous engineering activity.</p>
            </div>
            <span className="period-label">ALL TIME <span>⌄</span></span>
          </div>

          <div className="stats-grid">
            <article className="stat-card">
              <div className="stat-top"><span>Repositories analyzed</span><span className="stat-icon blue"><Icon name="git" /></span></div>
              <div className="stat-number">{analysisResult ? 1 : 0}</div>
              <div className="stat-foot">{analysisResult ? "Current session" : "No analysis yet"}</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Files analyzed</span><span className="stat-icon purple"><Icon name="code" /></span></div>
              <div className="stat-number">{readNumber(analysis?.total_files) ?? "—"}</div>
              <div className="stat-foot">
                {readNumber(analysis?.total_functions) ?? "—"} functions · {readNumber(analysis?.total_classes) ?? "—"} classes
              </div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Bugs in current repair</span><span className="stat-icon orange"><Icon name="alert" /></span></div>
              <div className="stat-number">{analysisResult ? bugsDetected || "—" : "—"}</div>
              <div className="stat-foot">{analysisResult ? (bugsDetected ? "From the current repair review" : "No bug count returned by analysis API") : "Run an analysis first"}</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Issues repaired</span><span className="stat-icon green"><Icon name="check" /></span></div>
              <div className="stat-number">{repairJob ? bugsRepaired : "—"}</div>
              <div className="stat-foot">{pendingReviews ? "1 patch awaiting approval" : "Based on repair audit log"}</div>
            </article>
          </div>

          <div className="lower-grid">
            <section className="jobs-panel" id="history">
              <div className="section-heading panel-heading">
                <div>
                  <h2>Recent repair jobs</h2>
                  <p>Latest repository analysis and repair activity.</p>
                </div>
                <span className="view-all">{historyTotal} total jobs</span>
              </div>

              <div className="jobs-list">
                {currentJobs.length === 0 ? (
                  <div className="empty-jobs">
                    No repair jobs yet. Start an autonomous repair to build your history.
                  </div>
                ) : (
                  currentJobs.map((job) => (
                    <article className="job-row" key={job.key}>
                      <div className="job-icon"><Icon name="code" /></div>
                      <div className="job-details">
                        <strong>{job.name}</strong>
                        <span>{job.detail}</span>
                      </div>
                      <div className="job-result">
                        <span className={`status-badge ${job.color}`}>{job.status}</span>
                      </div>
                      <span className="job-time">{job.time}</span>
                    </article>
                  ))
                )}
              </div>
            </section>

            <section className="activity-panel" id="activity">
              <div className="section-heading panel-heading">
                <div>
                  <h2>Agent activity</h2>
                  <p>Execution pipeline</p>
                </div>
                <span className="live-badge"><span /> LIVE</span>
              </div>

              <div className="pipeline">
                <div className="pipeline-step complete">
                  <div className="pipeline-marker"><Icon name="check" size={13} /></div>
                  <div><strong>Repository ingestion</strong><span>Source code indexed</span></div>
                  <span className="step-time">Done</span>
                </div>
                <div className="pipeline-step complete">
                  <div className="pipeline-marker"><Icon name="check" size={13} /></div>
                  <div><strong>Static analysis</strong><span>Ruff · Semgrep · pytest</span></div>
                  <span className="step-time">Done</span>
                </div>
                <div className="pipeline-step complete">
                  <div className="pipeline-marker"><Icon name="check" size={13} /></div>
                  <div><strong>AI bug detection</strong><span>Potential issues identified</span></div>
                  <span className="step-time">Done</span>
                </div>
                <div className="pipeline-step waiting">
                  <div className="pipeline-marker"><Icon name="clock" size={13} /></div>
                  <div><strong>Patch validation</strong><span>Awaiting next repair job</span></div>
                  <span className="step-time">Idle</span>
                </div>
              </div>

              <div className="activity-footer">
                <span className="activity-footer-icon"><Icon name="terminal" size={15} /></span>
                <span>Human-in-the-loop protection enabled</span>
              </div>
            </section>
          </div>

          <footer className="dashboard-footer">
            <span>ASEA <span className="footer-dot">●</span> Autonomous Software Engineering Agent</span>
            <span>Built for reliable autonomous engineering.</span>
          </footer>
        </div>
      </section>
    </main>
  );
}
