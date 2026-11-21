
"use client";

import { useEffect, useState } from "react";
import {
  analyzeRepository,
  startRepair,
  getRepairStatus,
  getRepairHistory,
  submitPatchReview,
  retryRepairPublishing,
  checkBackendHealth,
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

function describePatchChange(diff: string, fallback: string): string {
  if (/^\+\s*try\s*:/m.test(diff)) return "Restore missing try-block syntax";
  if (/^\-\s*total_score\s*=\s*1/m.test(diff) && /^\+\s*total_score\s*=\s*0/m.test(diff)) {
    return "Initialize the score accumulator at zero";
  }
  if (/^\+\s*if not scores\s*:/m.test(diff)) return "Handle empty score lists safely";
  return fallback || "Code change";
}

function describeAuditEvent(event: string): string {
  const descriptions: Record<string, string> = {
    patch_approved: "Human reviewer approved this issue's proposed patch.",
    patch_rejected: "Human reviewer rejected this issue's proposed patch.",
    syntax_repair_intermediate: "Syntax repair passed its intermediate check; ASEA continued analysis.",
    bug_repaired: "Validation passed and this issue was marked repaired.",
    patch_generation_failed: "ASEA could not generate a patch for this issue.",
    duplicate_patch_stopped: "ASEA stopped a repeated identical patch.",
  };
  return descriptions[event] ?? "Workflow event recorded.";
}

export default function Home() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [themeLoaded, setThemeLoaded] = useState(false);
  const [backendStatus, setBackendStatus] = useState<"checking" | "online" | "offline">("checking");
  const [repairHistory, setRepairHistory] = useState<RepairJob[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [publishRequested, setPublishRequested] = useState(false);
  const [repository, setRepository] = useState("");
  const [notice, setNotice] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
 const [analysisResult, setAnalysisResult] = useState<AnalyzeResponse | null>(null);
 const [isStartingRepair, setIsStartingRepair] = useState(false);
const [repairJob, setRepairJob] = useState<RepairResponse | null>(null);
const [repairStatus, setRepairStatus] = useState<RepairJob | null>(null);
const [reviewFeedback, setReviewFeedback] = useState("");
const [isSubmittingReview, setIsSubmittingReview] = useState(false);
const [isRetryingPublish, setIsRetryingPublish] = useState(false);

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

  async function checkBackend() {
    try {
      await checkBackendHealth();
      if (!cancelled) setBackendStatus("online");
    } catch {
      if (!cancelled) setBackendStatus("offline");
    }
  }

  void checkBackend();
  const healthTimer = setInterval(checkBackend, 10000);

  return () => {
    cancelled = true;
    clearInterval(healthTimer);
  };
}, []);

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

      if ((status.status === "completed" && !status.publishing_retry) || status.status === "failed") {
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
}, [repairJob?.job_id, repairStatus?.publishing_retry]);

  
async function startAnalysis() {
  const repoUrl = repository.trim();

  if (!repoUrl) {
    setNotice("Please enter a GitHub repository URL.");
    return;
  }

  if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?\/?$/i.test(repoUrl)) {
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
    const result = await startRepair(repository.trim(), publishRequested);

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
async function openRepairJob(job: RepairJob) {
  setRepairJob({
    job_id: job.job_id,
    status: job.status,
    message: job.message || "Loaded from repair history.",
  });
  setRepairStatus(null);
  setRepository(job.repository);
  setAnalysisResult(null);
  setReviewFeedback("");
  setNotice(`Loaded repair job ${job.job_id}.`);
  try {
    const status = await getRepairStatus(job.job_id);
    setRepairStatus(status);
  } catch (error) {
    setNotice(
      error instanceof Error ? error.message : "Unable to load this repair job.",
    );
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


async function handleRetryPublishing() {
  if (!repairJob?.job_id) return;
  setIsRetryingPublish(true);
  setNotice("Retrying GitHub publishing for the validated repair...");
  try {
    await retryRepairPublishing(repairJob.job_id);
    setNotice("Publishing retry submitted. ASEA will update this job when GitHub responds.");
    const status = await getRepairStatus(repairJob.job_id);
    setRepairStatus(status);
  } catch (error) {
    setNotice(error instanceof Error ? error.message : "Unable to retry GitHub publishing.");
  } finally {
    setIsRetryingPublish(false);
  }
}

  const analysis = analysisResult?.analysis;
  const preflight = readRecord(analysis?.preflight);
  const syntaxErrors = Array.isArray(analysis?.syntax_errors)
    ? analysis.syntax_errors.map(readRecord).filter(
        (entry): entry is Record<string, unknown> => entry !== null,
      )
    : [];
  const testFiles = Array.isArray(preflight?.test_files)
    ? preflight.test_files.filter((item): item is string => typeof item === "string")
    : [];
  const dependencyManifests = Array.isArray(preflight?.dependency_manifests)
    ? preflight.dependency_manifests.filter((item): item is string => typeof item === "string")
    : [];
  const testFrameworks = Array.isArray(preflight?.test_frameworks)
    ? preflight.test_frameworks.filter((item): item is string => typeof item === "string")
    : [];
  const reviewRequest = readRecord(repairStatus?.review_request);
  const reviewBug = readRecord(reviewRequest?.bug);
  const repairResult = readRecord(repairStatus?.result);
  const workflowResult = readRecord(repairResult?.workflow) ?? repairResult;
  const publishingResult = readRecord(repairResult?.publishing);
  const pullRequest = readRecord(publishingResult?.pull_request);
  const auditLog = Array.isArray(workflowResult?.audit_log)
    ? workflowResult.audit_log.map(readRecord).filter(
        (entry): entry is Record<string, unknown> => entry !== null,
      )
    : [];
  const validationHistory = Array.isArray(workflowResult?.validation_results)
    ? workflowResult.validation_results.map(readRecord).filter(
        (entry): entry is Record<string, unknown> => entry !== null,
      )
    : [];
  const detectedBugs = Array.isArray(workflowResult?.detected_bugs)
    ? workflowResult.detected_bugs.map(readRecord).filter(
        (entry): entry is Record<string, unknown> => entry !== null,
      )
    : [];
  const prioritizedBugs = Array.isArray(workflowResult?.prioritized_bugs)
    ? workflowResult.prioritized_bugs.map(readRecord).filter(
        (entry): entry is Record<string, unknown> => entry !== null,
      )
    : [];
  const repairedBugs = Array.isArray(workflowResult?.repaired_bugs)
    ? workflowResult.repaired_bugs.map(readRecord).filter((entry): entry is Record<string, unknown> => entry !== null)
    : [];
  const escalatedBugs = Array.isArray(workflowResult?.escalated_bugs)
    ? workflowResult.escalated_bugs.map(readRecord).filter((entry): entry is Record<string, unknown> => entry !== null)
    : [];
  const findBugTitle = (bugKey: unknown): string => {
    if (typeof bugKey !== "string") return "";
    const candidates = [
      ...prioritizedBugs,
      ...detectedBugs,
      ...repairedBugs,
      ...escalatedBugs,
      ...validationHistory,
    ];
    const match = candidates.find((item) => item.bug_key === bugKey);
    const title = match?.title ?? match?.bug_title;
    return typeof title === "string" ? title : "";
  };
  const patchHistory = Array.isArray(workflowResult?.patches)
    ? workflowResult.patches.map(readRecord).filter((entry): entry is Record<string, unknown> => entry !== null)
    : [];
  const finalTests = readRecord(workflowResult?.final_validation_results)
    ?? readRecord(workflowResult?.working_test_results);
  const finalValidationPassed =
    repairStatus?.status === "completed"
    && finalTests !== null
    && Number(finalTests.passed ?? 0) > 0
    && Number(finalTests.failed ?? 0) === 0
    && Number(finalTests.errors ?? 0) === 0
    && repairedBugs.length > 0;
  // The dashboard falls back to the newest persisted repair when no job is
  // currently open, so these metrics remain meaningful after a page refresh.
  const dashboardJob = repairStatus ?? repairHistory[0] ?? null;
  const dashboardResult = readRecord(dashboardJob?.result);
  const dashboardWorkflow =
    readRecord(dashboardResult?.workflow) ?? dashboardResult;
  const dashboardPrioritizedBugs = Array.isArray(dashboardWorkflow?.prioritized_bugs)
    ? dashboardWorkflow.prioritized_bugs
    : Array.isArray(dashboardWorkflow?.detected_bugs)
      ? dashboardWorkflow.detected_bugs
      : [];
  const dashboardRepairedBugs = Array.isArray(dashboardWorkflow?.repaired_bugs)
    ? dashboardWorkflow.repaired_bugs
    : [];
  const hasRepairMetrics = dashboardWorkflow !== null;
  const bugsDetected = reviewBug
    ? 1
    : dashboardPrioritizedBugs.filter((bug) => readRecord(bug) !== null).length;
  const bugsRepaired = dashboardRepairedBugs.length;
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


  const lifecycleIndex =
    repairStatus?.status === "queued" ? 0 :
    repairStatus?.status === "running" ? 1 :
    repairStatus?.status === "waiting_for_review" ? 2 :
    repairStatus?.status === "completed" ? 3 : -1;
  const lifecycleFailed = repairStatus?.status === "failed";
  const lifecycleSteps = [
    { title: "Repair job submitted", description: "ASEA accepted the repository job." },
    { title: "Autonomous repair", description: "Agents analyze issues and generate candidate patches." },
    { title: "Human patch review", description: "Review and approve or reject proposed changes." },
    { title: "Final validation & publishing", description: "Run final tests and publish only after approval." },
  ];

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
              <span className={`engine-dot ${backendStatus}`} /> ENGINE STATUS
            </div>
            <div className="engine-status">
              {backendStatus === "online" ? "Backend online" : backendStatus === "offline" ? "Backend offline" : "Checking backend"}
            </div>
            <div className="engine-description">
              {backendStatus === "online" ? "ASEA API health check succeeded." : backendStatus === "offline" ? "Start the FastAPI backend to enable analysis and repair." : "Checking the ASEA API connection."}
            </div>
          </div>
          <a className="nav-item" href="/settings">
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
            <span className={`system-indicator ${backendStatus}`}><span /> {backendStatus === "online" ? "Backend connected" : backendStatus === "offline" ? "Backend unavailable" : "Checking backend"}</span>
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
            <label className="publish-option">
              <input
                type="checkbox"
                checked={publishRequested}
                onChange={(event) => setPublishRequested(event.target.checked)}
              />
              <span>
                <strong>Create a GitHub pull request after validation</strong>
                <small>Only approved repairs that pass final tests will be published. Backend GitHub credentials are required.</small>
              </span>
            </label>
            {notice && !["completed", "failed"].includes(String(repairStatus?.status ?? "")) && (
  <div className="input-notice" role="status">
    {notice}
  </div>
)}

{analysisResult && (
  <div className="analysis-result">
    <div className="analysis-heading">
      <div>
        <strong>Repository checks completed</strong>
        <p>{String(analysis?.repository ?? "Python repository")}</p>
      </div>
      <span className={syntaxErrors.length === 0 || finalValidationPassed ? "check-status passed" : "check-status failed"}>
        {syntaxErrors.length === 0
          ? "Syntax checks passed"
          : finalValidationPassed
            ? "Initial syntax findings resolved"
            : `${syntaxErrors.length} syntax issue(s)`}
      </span>
    </div>
    <div className="preflight-grid">
      <div className="preflight-metric">
        <span>Python files</span>
        <strong>{String(preflight?.python_file_count ?? analysis?.total_files ?? 0)}</strong>
      </div>
      <div className="preflight-metric">
        <span>Test files</span>
        <strong>{String(preflight?.test_file_count ?? 0)}</strong>
      </div>
      <div className="preflight-metric">
        <span>Functions</span>
        <strong>{String(analysis?.total_functions ?? 0)}</strong>
      </div>
      <div className="preflight-metric">
        <span>Classes</span>
        <strong>{String(analysis?.total_classes ?? 0)}</strong>
      </div>
    </div>
    <div className="preflight-details">
      <div>
        <b>Test framework</b>
        <p>{testFrameworks.length ? testFrameworks.join(", ") : "Not detected"}</p>
      </div>
      <div>
        <b>Dependency files</b>
        <p>{dependencyManifests.length ? dependencyManifests.join(", ") : "No manifest found"}</p>
      </div>
      <div>
        <b>Existing tests</b>
        {testFiles.length
          ? <ul>{testFiles.slice(0, 8).map((file) => <li key={file}>{file}</li>)}</ul>
          : <p>No existing Python test files found</p>}
      </div>
    </div>
    {syntaxErrors.length > 0 && (
      <div
        className={`syntax-error-list${finalValidationPassed ? " resolved" : ""}`}
        role={repairStatus?.status === "completed" ? "status" : "alert"}
      >
        <b>
          {finalValidationPassed
            ? "Initial repository findings — resolved"
            : repairStatus?.status === "completed"
              ? "Findings from initial repository analysis"
              : "Syntax errors to fix before repair"}
        </b>
        {finalValidationPassed ? (
          <p>All listed initial syntax findings were addressed by the repair. Final validation passed ({String(finalTests?.passed ?? 0)} passed, {String(finalTests?.failed ?? 0)} failed, {String(finalTests?.errors ?? 0)} errors). The original findings are retained below for reference.</p>
        ) : repairStatus?.status === "completed" ? (
          <p>These issues were detected before repair. They remain listed as initial findings because successful final validation has not been confirmed.</p>
        ) : null}
        {syntaxErrors.map((error, index) => (
          <p key={`${String(error.file)}-${index}`}>
            <code>{String(error.file ?? "Unknown file")}</code>
            {": "}{String(error.message ?? "Syntax error")}
            {error.line ? ` (line ${String(error.line)})` : ""}
            {finalValidationPassed && <strong> · Resolved</strong>}
          </p>
        ))}
      </div>
    )}
    <details className="raw-analysis">
      <summary>View complete analysis data</summary>
      <pre>{JSON.stringify(analysisResult.analysis, null, 2)}</pre>
    </details>
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

    <div className="patch-diagnostics">
      <p><b>Generator:</b> {String(reviewRequest?.generator ?? "unknown")}</p>
      <p><b>Repair scope:</b> {String(reviewRequest?.repair_scope ?? "complete")}</p>
      {Array.isArray(reviewRequest?.generation_trace) && reviewRequest.generation_trace.length > 0 && (
        <details>
          <summary>Generation diagnostics ({reviewRequest.generation_trace.length} steps)</summary>
          {reviewRequest.generation_trace.map((item, index) => {
            const trace = readRecord(item);
            if (!trace) return null;
            return (
              <article key={index}>
                <strong>{String(trace.stage ?? "generation")} · {String(trace.status ?? "unknown")}</strong>
                {typeof trace.error === "string" && <p>{trace.error}</p>}
                {typeof trace.response_excerpt === "string" && (
                  <pre>{trace.response_excerpt}</pre>
                )}
                {typeof trace.reason === "string" && <p>{trace.reason}</p>}
              </article>
            );
          })}
        </details>
      )}
      {typeof reviewRequest?.patch === "string" && (
        <details className="patch-preview" open>
          <summary>Proposed code diff · attempt {String(reviewRequest.attempt ?? 1)}</summary>
          <pre className="patch-diff">{reviewRequest.patch}</pre>
        </details>
      )}
      <details>
        <summary>Full review payload</summary>
        <pre>{JSON.stringify(repairStatus.review_request, null, 2)}</pre>
      </details>
    </div>

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

    {publishingResult?.status === "PUBLISH_FAILED" && (
      <>
      <p role="alert">
        <strong>Repair validated; GitHub publishing failed.</strong>{" "}
        {typeof publishingResult.error === "string"
          ? publishingResult.error
          : "Check the backend GitHub token and repository permissions."}
      </p>
      <button
        className="repair-button publish-retry-button"
        type="button"
        onClick={handleRetryPublishing}
        disabled={isRetryingPublish || Boolean(repairStatus?.publishing_retry)}
      >
        {isRetryingPublish || repairStatus?.publishing_retry ? "Publishing retry in progress..." : "Retry GitHub publishing"}
      </button>
      </>
    )}
    {publishingResult?.status === "PULL_REQUEST_CREATED" && !pullRequest && (
      <p><strong>GitHub publishing completed.</strong> Pull request details were not included in the response.</p>
    )}
    {repairStatus?.status === "completed" && !publishingResult && (
      <p>
        <strong>Repair completed.</strong>{" "}
        {repairStatus.publish_requested
          ? "Publishing was requested but no pull-request result was recorded."
          : "No pull request was requested for this job."}
      </p>
    )}

    {pullRequest && typeof pullRequest.url === "string" && (
      <p className="pull-request-link">
        <strong>Pull request created:</strong>{" "}
        <a href={String(pullRequest.url)} target="_blank" rel="noreferrer">
          {String(pullRequest.url)}
        </a>
      </p>
    )}

    {repairStatus?.result && (
      <section className="repair-report">
        <h3>Repair report</h3>
        {finalTests && (
          <div className="test-summary" aria-label="Final test results">
            <div><strong>{String(finalTests.passed ?? 0)}</strong><span>Passed</span></div>
            <div><strong>{String(finalTests.failed ?? 0)}</strong><span>Failed</span></div>
            <div><strong>{String(finalTests.errors ?? 0)}</strong><span>Errors</span></div>
            <div><strong>{String(finalTests.total ?? 0)}</strong><span>Total</span></div>
          </div>
        )}
        <div className="repair-report-grid">
          <div><span>Repaired bugs</span><strong>{repairedBugs.length}</strong></div>
          <div><span>Escalated bugs</span><strong>{escalatedBugs.length}</strong></div>
          <div><span>Patches generated</span><strong>{patchHistory.length}</strong></div>
          <div><span>Audit events</span><strong>{auditLog.length}</strong></div>
        </div>
        {repairedBugs.length > 0 && (
          <div className="report-list"><h4>Repaired issues</h4>{repairedBugs.map((bug, index) => (
            <article key={String(bug.bug_key ?? index)}><strong>{String(bug.title ?? "Repaired issue")}</strong><span>{String(bug.file ?? "File not recorded")} · {String(bug.severity ?? "severity unknown")}</span></article>
          ))}</div>
        )}
        {escalatedBugs.length > 0 && (
          <div className="report-list"><h4>Needs attention</h4>{escalatedBugs.map((bug, index) => (
            <article key={String(bug.bug_key ?? index)}><strong>{String(bug.title ?? "Unresolved issue")}</strong><span>{String(bug.file ?? "File not recorded")} · {String(bug.reason ?? "Could not be repaired")}</span></article>
          ))}</div>
        )}
        {patchHistory.length > 0 && (
          <details className="patch-history">
            <summary>Patch history ({patchHistory.length} patches)</summary>
            <p className="patch-history-note">Patch numbers show the order of fixes. Attempt numbers count retries for the same issue.</p>
            {patchHistory.map((patch, index) => {
              const diff = typeof patch.patch === "string" ? patch.patch : "";
              const attempt = Number(patch.attempt ?? 1);
              const validation = [...validationHistory].reverse().find(
                (item) => item.bug_key === patch.bug_key && Number(item.attempt ?? 1) === attempt,
              );
              const validationStatus = String(validation?.status ?? "NOT_RECORDED");
              const validationTests = readRecord(validation?.test_results);
              const issueTitle = String(
                patch.bug_title ?? validation?.bug_title ?? findBugTitle(patch.bug_key) ?? "",
              );
              const patchSummary = describePatchChange(diff, issueTitle);
              const fileName = String(patch.file ?? "File not recorded").split(/[\\/]/).pop();
              const validationClass = ["PATCH_VALIDATED", "PATCH_INTERMEDIATE"].includes(validationStatus)
                ? "passed"
                : validationStatus === "PATCH_REJECTED" ? "failed" : "pending";
              return (
                <details className="patch-history-record" key={`${String(patch.bug_key ?? "patch")}-${attempt}-${index}`}>
                  <summary>
                    <span className="patch-record-heading">Patch {index + 1} of {patchHistory.length}: {patchSummary}</span>
                    <span className="patch-record-meta">{fileName} · attempt {attempt} · {formatStatus(String(patch.status ?? "status unknown"))}</span>
                  </summary>
                  <div className="patch-validation-row">
                    <strong>Validation: </strong>
                    <span className={`patch-validation-status ${validationClass}`}>{formatStatus(validationStatus)}</span>
                    {validationTests && (
                      <span className="patch-validation-counts">
                        {String(validationTests.passed ?? 0)} passed · {String(validationTests.failed ?? 0)} failed · {String(validationTests.errors ?? 0)} errors
                      </span>
                    )}
                    {!validation && <span className="patch-validation-counts">No per-patch validation record was found.</span>}
                  </div>
                  {typeof patch.patch === "string"
                    ? <pre className="patch-diff">{patch.patch}</pre>
                    : <p className="patch-record-reason">{String(patch.reason ?? "No diff was recorded for this patch.")}</p>}
                </details>
              );
            })}
          </details>
        )}
        {auditLog.length > 0 && (
          <details className="audit-history">
            <summary>Agent activity log ({auditLog.length} events)</summary>
            <ol>{auditLog.map((entry, index) => {
              const event = String(entry.event ?? "event");
              const issueTitle = String(entry.bug_title ?? findBugTitle(entry.bug_key) ?? "");
              const details = [
                issueTitle || "Workflow-level event",
                String(entry.agent ?? "ASEA agent"),
                entry.attempt !== undefined ? `attempt ${String(entry.attempt)}` : "",
              ].filter(Boolean).join(" · ");
              return (
                <li key={`${event}-${String(entry.bug_key ?? "workflow")}-${index}`}>
                  <strong>{formatStatus(event)}</strong>
                  <span>{describeAuditEvent(event)}</span>
                  <small>{details}</small>
                </li>
              );
            })}</ol>
          </details>
        )}
        {finalTests && typeof finalTests.output === "string" && finalTests.output && (
          <details><summary>Final test output</summary><pre className="patch-diff">{finalTests.output}</pre></details>
        )}
        <details className="raw-analysis"><summary>View complete workflow data</summary><pre>{JSON.stringify(repairStatus.result, null, 2)}</pre></details>
      </section>
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
              <div className="stat-number">{hasRepairMetrics ? bugsDetected : "—"}</div>
              <div className="stat-foot">{hasRepairMetrics ? "Confirmed bugs in the latest repair" : "Available after a repair job starts"}</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Issues repaired</span><span className="stat-icon green"><Icon name="check" /></span></div>
              <div className="stat-number">{hasRepairMetrics ? bugsRepaired : "—"}</div>
              <div className="stat-foot">{pendingReviews ? "1 patch awaiting approval" : "Repaired issues in the latest repair"}</div>
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
                        <button
                          className="job-open"
                          type="button"
                          onClick={() => {
                            const selected = repairHistory.find((item) => item.job_id === job.key);
                            if (selected) void openRepairJob(selected);
                          }}
                        >
                          Open job
                        </button>
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
                <span className="live-badge"><span /> {repairStatus && ["queued", "running", "waiting_for_review"].includes(repairStatus.status) ? "LIVE" : "IDLE"}</span>
              </div>

              <div className="pipeline">
                {lifecycleSteps.map((step, index) => {
                  const complete = !lifecycleFailed && lifecycleIndex >= 0 &&
                    (lifecycleIndex > index || repairStatus?.status === "completed");
                  const active = !lifecycleFailed && lifecycleIndex === index &&
                    repairStatus?.status !== "completed";
                  const failed = lifecycleFailed && index === 1;
                  const stateClass = complete ? "complete" : active ? "active" : failed ? "failed" : "waiting";
                  const stateLabel = complete ? "Done" : active ? "Active" : failed ? "Stopped" : "Waiting";
                  return (
                    <div className={`pipeline-step ${stateClass}`} key={step.title}>
                      <div className="pipeline-marker">
                        {complete ? <Icon name="check" size={13} /> : failed ? <Icon name="alert" size={13} /> : <Icon name="clock" size={13} />}
                      </div>
                      <div><strong>{step.title}</strong><span>{step.description}</span></div>
                      <span className="step-time">{stateLabel}</span>
                    </div>
                  );
                })}
                {repairStatus?.status === "failed" && repairStatus.error && (
                  <p className="pipeline-error" role="alert">{repairStatus.error}</p>
                )}
                {repairStatus?.message && <p className="pipeline-live-message" role="status">{repairStatus.message}</p>}
                {repairStatus?.created_at && (
                  <div className="pipeline-timestamps">
                    <span>Started {new Date(repairStatus.created_at).toLocaleString()}</span>
                    {repairStatus.updated_at && <span>Updated {new Date(repairStatus.updated_at).toLocaleString()}</span>}
                  </div>
                )}
                {!repairJob && <p className="pipeline-idle">Start a repair to see its live lifecycle here.</p>}
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
