
const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export interface AnalyzeResponse {
  status: string;
  message: string;
  analysis: Record<string, unknown>;
}

export interface RepairResponse {
  message: string;
  job_id: string;
  status: string;
}

export interface Publication {
  status: string;
  reason?: string;
  repository?: string;
  branch?: string;
  base_branch?: string;
  pushed_to_fork?: boolean;
  files?: string[];
  pull_request?: { url?: string; number?: number } | null;
}

export interface RepairJob {
  job_id: string;
  repository: string;
  status: string;
  stage?: string | null;
  elapsed_seconds?: number;
  is_active?: boolean;
  is_terminal?: boolean;
  timings?: Record<string, number> | null;
  publish?: boolean;
  publication?: Publication | null;
  message?: string;
  error?: string | null;
  review_request?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
}

export interface ReviewRequest {
  decision: "approved" | "rejected";
  feedback: string;
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${API_URL}${endpoint}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new Error(
      "Cannot connect to ASEA backend. Make sure FastAPI is running on port 8000.",
    );
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      data.detail || `API request failed with status ${response.status}.`,
    );
  }

  return data as T;
}

export function analyzeRepository(
  repository: string,
): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>("/analyze", {
    method: "POST",
    body: JSON.stringify({ repository }),
  });
}

export function startRepair(
  repository: string,
  publish = true,
): Promise<RepairResponse> {
  return request<RepairResponse>("/repair", {
    method: "POST",
    body: JSON.stringify({ repository, publish }),
  });
}

export function getRepairStatus(
  jobId: string,
): Promise<RepairJob> {
  return request<RepairJob>(
    `/repair/${encodeURIComponent(jobId)}`,
  );
}

export function submitPatchReview(
  jobId: string,
  review: ReviewRequest,
): Promise<RepairResponse> {
  return request<RepairResponse>(
    `/repair/${encodeURIComponent(jobId)}/review`,
    {
      method: "POST",
      body: JSON.stringify(review),
    },
  );
}

export function checkBackendHealth(): Promise<{
  status: string;
  service: string;
}> {
  return request("/health");
}
