import React from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { FeedbackBanner } from "../ui/feedback";

export function LoginPage(): React.ReactElement {
  const { user, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const from = (location.state as { from?: string } | undefined)?.from ?? "/dashboard";
  if (user) return <Navigate to={from} replace />;

  async function onSubmit(e: React.FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось выполнить вход");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto mt-20 max-w-md rounded-lg border bg-white p-6 shadow-sm">
      <h1 className="text-2xl font-semibold">Вход</h1>
      <p className="mt-1 text-sm text-slate-600">Доступ для админа и наблюдателя</p>
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
          placeholder="Пароль"
          required
        />
        <button
          className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          type="submit"
          disabled={loading}
        >
          {loading ? "Вход..." : "Войти"}
        </button>
      </form>
      <p className="mt-4 text-sm text-slate-600">
        Нет аккаунта?{" "}
        <Link className="underline" to="/register">
          Зарегистрироваться
        </Link>
      </p>
    </div>
  );
}
