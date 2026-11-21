"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getAccessToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "") ?? "";

type Worker = {
  worker_id: string;
  status: string;
  worker_version?: string | null;
  capabilities?: Record<string, unknown>;
  last_heartbeat_at?: string | null;
  created_at?: string | null;
};

type WorkerList = { items: Worker[] };
type EnrollmentCode = { enrollment_code: string; expires_at: string };

async function apiRequest<T>(path: string, method = "GET"): Promise<T> {
  if (!API_URL) throw new Error("NEXT_PUBLIC_API_URL is not configured.");
  const token = await getAccessToken();
  if (!token) throw new Error("Your ASEA session has expired. Sign in again.");
  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status}).`);
  return data as T;
}

function formatDate(value?: string | null): string {
  if (!value) return "Not yet connected";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleString();
}

export default function SettingsPage() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [enrollment, setEnrollment] = useState<EnrollmentCode | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const loadWorkers = useCallback(async () => {
    const result = await apiRequest<WorkerList>("/v1/workers");
    setWorkers(result.items ?? []);
  }, []);

  useEffect(() => {
    let active = true;
    void apiRequest<WorkerList>("/v1/workers")
      .then((result) => { if (active) setWorkers(result.items ?? []); })
      .catch((error: unknown) => {
        if (active) setMessage(error instanceof Error ? error.message : "Unable to load workers.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function createEnrollmentCode() {
    setBusy(true);
    setMessage("");
    setEnrollment(null);
    try {
      const result = await apiRequest<EnrollmentCode>("/v1/workers/enrollment-code", "POST");
      setEnrollment(result);
      setMessage("One-time enrollment code created. It expires in 10 minutes; use it only on your own Windows worker.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not create an enrollment code.");
    } finally {
      setBusy(false);
    }
  }

  async function revokeWorker(workerId: string) {
    if (!window.confirm("Revoke this worker? It will no longer be able to claim jobs.")) return;
    setBusy(true);
    setMessage("");
    try {
      await apiRequest(`/v1/workers/${encodeURIComponent(workerId)}/revoke`, "POST");
      await loadWorkers();
      setMessage("Worker revoked.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not revoke worker.");
    } finally {
      setBusy(false);
    }
  }

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setMessage("Copied to clipboard.");
    } catch {
      setMessage("Clipboard access was blocked. Select and copy the code manually.");
    }
  }

  return (
    <main className="settings-page">
      <Link className="settings-back" href="/">← Back to dashboard</Link>
      <div className="eyebrow" style={{ marginTop: 24 }}>WORKSPACE CONFIGURATION</div>
      <h1>Settings</h1>
      <p className="settings-lead">
        Connect and manage the Windows machine that executes ASEA repair jobs.
        Your Render API coordinates jobs; the enrolled local worker runs repository
        analysis, Ollama, Docker, and tests on your machine.
      </p>

      <section className="settings-panel">
        <h2>Cloud worker enrollment</h2>
        <p>
          Create a short-lived, single-use code, then enroll your Windows machine
          from the ASEA repository folder. The worker credential is saved in the
          Windows credential vault, not in this browser.
        </p>
        <button className="settings-button primary" type="button" onClick={createEnrollmentCode} disabled={busy}>
          {busy ? "Please wait…" : "Generate enrollment code"}
        </button>
        {enrollment && (
          <div className="settings-message">
            <strong>One-time code (expires {formatDate(enrollment.expires_at)})</strong>
            <code className="settings-command">{enrollment.enrollment_code}</code>
            <button className="settings-button" type="button" onClick={() => void copy(enrollment.enrollment_code)}>Copy code</button>
            <p>Do not share this code. Enter it only into the hidden prompt from the local enrollment command.</p>
          </div>
        )}
      </section>

      <section className="settings-panel">
        <h2>Windows setup commands</h2>
        <p>Open PowerShell in your local ASEA repository folder and run:</p>
        <code className="settings-command">python -m pip install -e ".[cloud-worker]"</code>
        <code className="settings-command">asea cloud-worker enroll --api-url {API_URL || "https://YOUR-RENDER-SERVICE.onrender.com"}</code>
        <p>When prompted, paste the one-time enrollment code. Then check the enrollment:</p>
        <code className="settings-command">asea cloud-worker status --api-url {API_URL || "https://YOUR-RENDER-SERVICE.onrender.com"}</code>
        <p>
          Before running jobs, make sure Ollama and Docker are running and that
          <code> ASEA_DATABASE_URL </code> is configured in the worker machine's
          environment using your Supabase PostgreSQL connection string. Keep that
          secret out of the browser and Git repository.
        </p>
        <code className="settings-command">asea cloud-worker run --api-url {API_URL || "https://YOUR-RENDER-SERVICE.onrender.com"}</code>
        <p>Keep this PowerShell process open while you want ASEA to execute jobs.</p>
      </section>

      <section className="settings-panel">
        <h2>Enrolled workers</h2>
        <p>Workers are scoped to your signed-in account. Last heartbeat shows when the machine last contacted ASEA.</p>
        <div className="settings-actions">
          <button className="settings-button" type="button" onClick={() => { setLoading(true); void loadWorkers().catch((error: unknown) => setMessage(error instanceof Error ? error.message : "Refresh failed.")).finally(() => setLoading(false)); }} disabled={loading || busy}>Refresh workers</button>
        </div>
        {loading ? <p>Loading workers…</p> : workers.length === 0 ? (
          <p>No workers enrolled yet. Generate a code above to connect this machine.</p>
        ) : (
          <div className="worker-list">
            {workers.map((worker) => (
              <article className="worker-item" key={worker.worker_id}>
                <div>
                  <strong>{worker.worker_id}</strong>
                  <small>Version: {worker.worker_version || "Not reported"} · Last heartbeat: {formatDate(worker.last_heartbeat_at)}</small>
                </div>
                <div className="settings-actions">
                  <span className="worker-state">{worker.status}</span>
                  {worker.status === "active" && (
                    <button className="settings-button" type="button" onClick={() => void revokeWorker(worker.worker_id)} disabled={busy}>Revoke</button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      {message && <div className="settings-message" role="status">{message}</div>}
    </main>
  );
}
