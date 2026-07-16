const BASE = '/api';
const TOKEN_KEY = 'flight_analyzer_session_token';
const SERVER_TOKEN_KEY = 'flight_analyzer_server_token';

export function getSessionToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function setSessionToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getServerToken(): string {
  return localStorage.getItem(SERVER_TOKEN_KEY) || '';
}

export function setServerToken(token: string | null) {
  if (token) localStorage.setItem(SERVER_TOKEN_KEY, token);
  else localStorage.removeItem(SERVER_TOKEN_KEY);
}

function parseErrorBody(text: string, fallback: string): string {
  if (!text) return fallback;
  try {
    const body = JSON.parse(text);
    const rawDetail = body.detail || body.error || fallback;
    const detail = typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail);
    const errorType = body.error_type ? ` (${body.error_type})` : '';
    return `${detail}${errorType}`;
  } catch {
    return text || fallback;
  }
}

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let response: Response;
  const token = getSessionToken();
  const serverToken = getServerToken();
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(serverToken ? { 'x-server-token': serverToken } : {}),
    ...(options?.headers ?? {}),
  };
  try {
    response = await fetch(`${BASE}${url}`, { ...options, headers });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`无法连接到后端服务，请确认应用已正常启动。网络错误：${message}`, {
      cause: error,
    });
  }
  if (!response.ok) {
    const text = await response.text();
    const detail = parseErrorBody(text, response.statusText);
    throw new Error(`${detail || '请求失败'} (HTTP ${response.status})`);
  }
  return response.json();
}
