const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim().replace(/\/$/, "");
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
const SESSION_KEY = "asea-supabase-session";
let refreshInFlight: Promise<string | null> | null = null;

export interface SupabaseSession {
  access_token: string;
  refresh_token: string;
  expires_at?: number;
  expires_in?: number;
  token_type?: string;
  user?: { id: string; email?: string };
}

interface AuthError {
  msg?: string;
  message?: string;
  error_description?: string;
}

export function isSupabaseAuthConfigured(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}

function readStoredSession(): SupabaseSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SupabaseSession;
    return parsed.access_token && parsed.refresh_token ? parsed : null;
  } catch {
    window.sessionStorage.removeItem(SESSION_KEY);
    return null;
  }
}

function storeSession(session: SupabaseSession | null): void {
  if (typeof window === "undefined") return;
  if (session) window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  else window.sessionStorage.removeItem(SESSION_KEY);
}

async function authRequest<T>(path: string, body: Record<string, string>): Promise<T> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    throw new Error("Configure NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.");
  }
  const response = await fetch(`${SUPABASE_URL}/auth/v1/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: SUPABASE_ANON_KEY,
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({})) as AuthError & Partial<SupabaseSession>;
  if (!response.ok) {
    throw new Error(data.message || data.msg || data.error_description || "Supabase authentication failed.");
  }
  return data as T;
}

export async function signIn(email: string, password: string): Promise<SupabaseSession> {
  const session = await authRequest<SupabaseSession>("token?grant_type=password", { email, password });
  if (!session.access_token || !session.refresh_token) {
    throw new Error("Sign-in succeeded without a session. Check whether email confirmation is required.");
  }
  storeSession(session);
  return session;
}

export async function signUp(email: string, password: string): Promise<SupabaseSession | null> {
  const session = await authRequest<SupabaseSession>("signup", { email, password });
  if (session.access_token && session.refresh_token) {
    storeSession(session);
    return session;
  }
  return null;
}

export function signOut(): void {
  const session = readStoredSession();
  storeSession(null);
  if (session?.access_token && SUPABASE_URL && SUPABASE_ANON_KEY) {
    void fetch(`${SUPABASE_URL}/auth/v1/logout`, {
      method: "POST",
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${session.access_token}`,
      },
    }).catch(() => undefined);
  }
}

export async function getAccessToken(): Promise<string | null> {
  const session = readStoredSession();
  if (!session) return null;

  const expiry = session.expires_at ?? (
    session.expires_in ? Math.floor(Date.now() / 1000) + session.expires_in : 0
  );
  if (expiry && expiry > Math.floor(Date.now() / 1000) + 60) {
    return session.access_token;
  }

  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const refreshed = await authRequest<SupabaseSession>("token?grant_type=refresh_token", {
        refresh_token: session.refresh_token,
      });
      if (!refreshed.access_token || !refreshed.refresh_token) {
        throw new Error("Refresh returned no session.");
      }
      storeSession(refreshed);
      return refreshed.access_token;
    } catch {
      storeSession(null);
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export function getStoredSession(): SupabaseSession | null {
  return readStoredSession();
}
