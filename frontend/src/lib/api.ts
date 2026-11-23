
import { getAccessToken } from "@/lib/auth";

const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
const API_URL =
  configuredApiUrl ||
  (process.env.NODE_ENV === "production"
    ? ""
    : "http://127.0.0.1:8000");

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

export interface RepairJob {
  job_id: string;
  repository: string;
  created_at?: string;
  status: string;
  message?: string;
  error?: string | null;
  review_request?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  publish_requested?: boolean;
  publishing_retry?: boolean;
  updated_at?: string;
}

export interface ReviewRequest {
  decision: "approved" | "rejected";
  feedback: string;
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  if (!API_URL) {
    throw new Error(
      "NEXT_PUBLIC_API_URL must be configured for production builds.",
    );
  }

  let response: Response;
  const accessToken = await getAccessToken();

  try {
    response = await fetch(`${API_URL}${endpoint}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...options.headers,
      },
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
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
  publish = false,
): Promise<RepairResponse> {
  return request<RepairResponse>("/repair", {
    method: "POST",
    body: JSON.stringify({ repository, publish }),
  });
}

export function getRepairStatus(
  jobId: string,
  signal?: AbortSignal,
): Promise<RepairJob> {
  return request<RepairJob>(
    `/repair/${encodeURIComponent(jobId)}`,
    { signal },
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

export function checkBackendHealth(
  signal?: AbortSignal,
): Promise<{
  status: string;
  service: string;
}> {
  return request("/health", { signal });
}

export interface RepairHistoryResponse {
  items: RepairJob[];
  total: number;
  limit: number;
  offset: number;
}

export function getRepairHistory(
  limit = 20,
  offset = 0,
  signal?: AbortSignal,
): Promise<RepairHistoryResponse> {
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return request<RepairHistoryResponse>(`/repairs?${query.toString()}`, {
    signal,
  });
}


export function retryRepairPublishing(
  jobId: string,
): Promise<RepairResponse> {
  return request<RepairResponse>(
    `/repair/${encodeURIComponent(jobId)}/publish`,
    { method: "POST" },
  );
}
