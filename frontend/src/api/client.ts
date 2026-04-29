export type ApiError = {
  detail?: unknown;
};

const baseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";
export const AUTH_TOKEN_KEY = "grs_access_token";

export function getStoredAccessToken(): string | null {
  return window.localStorage.getItem(AUTH_TOKEN_KEY);
}

function humanizeErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail !== "string") return fallback;
  const known: Record<string, string> = {
    "Email already registered": "Пользователь с такой почтой уже зарегистрирован",
    "Invalid email or password": "Неверная почта или пароль",
    "Pending request already exists": "Заявка уже отправлена и ожидает рассмотрения",
    "User already has admin role": "У вас уже есть права администратора",
    "Request not found": "Заявка не найдена",
    "Request already reviewed": "Заявка уже рассмотрена",
    "Not authenticated": "Необходимо войти в систему",
    "Invalid token": "Сессия недействительна, войдите снова",
    "User not found": "Пользователь не найден",
    "Insufficient permissions": "Недостаточно прав",
  };
  return known[detail] ?? detail;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredAccessToken();
  let res: Response;
  try {
    res = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch (e) {
    if (e instanceof TypeError) {
      throw new Error("Не удалось подключиться к серверу. Проверьте, что backend запущен и доступен.");
    }
    throw e;
  }

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as ApiError;
      msg = humanizeErrorDetail(body?.detail, msg);
    } catch {
      // ignore JSON parse errors
    }
    if (res.status === 422) msg = "Проверьте корректность заполнения формы";
    throw new Error(msg);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}

