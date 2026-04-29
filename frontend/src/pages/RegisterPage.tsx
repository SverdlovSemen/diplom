import React from "react";
import { Link, Navigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { FeedbackBanner } from "../ui/feedback";

function registrationErrorMessage(err: unknown): string {
  if (!(err instanceof Error)) return "Не удалось зарегистрироваться";
  if (err.message === "Failed to fetch") {
    return "Не удалось подключиться к серверу. Проверьте, что backend запущен.";
  }
  return err.message;
}

export function RegisterPage(): React.ReactElement {
  const { user, register } = useAuth();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  if (user) return <Navigate to="/dashboard" replace />;

  async function onSubmit(e: React.FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError("Пароли не совпадают");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await register(email, password);
    } catch (err) {
      setError(registrationErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto mt-20 max-w-md rounded-lg border bg-white p-6 shadow-sm">
      <h1 className="text-2xl font-semibold">Регистрация</h1>
      <p className="mt-1 text-sm text-slate-600">
        Новый аккаунт создается с ролью наблюдателя. Запрос на роль администратора подается после входа.
      </p>
      {error ? <div className="mt-4"><FeedbackBanner tone="error" message={error} /></div> : null}
      <form className="mt-4 space-y-3" onSubmit={(e) => void onSubmit(e)}>
        <input
          className="w-full rounded border px-3 py-2"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Электронная почта"
          required
        />
        <input
          className="w-full rounded border px-3 py-2"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Пароль (не менее 8 символов)"
          minLength={8}
          required
        />
        <input
          className="w-full rounded border px-3 py-2"
          type="password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          placeholder="Повторите пароль"
          minLength={8}
          required
        />
        <button
          className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          type="submit"
          disabled={loading}
        >
          {loading ? "Регистрация..." : "Создать аккаунт"}
        </button>
      </form>
      <p className="mt-4 text-sm text-slate-600">
        Уже есть аккаунт?{" "}
        <Link className="underline" to="/login">
          Войти
        </Link>
      </p>
    </div>
  );
}
