"use client";

import {
  FormEvent,
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { API_URL } from "./format";
import type { ServiceStatus, Session } from "./types";

type SessionContextValue = {
  session: Session;
  serviceStatus: ServiceStatus;
  setServiceStatus: (status: ServiceStatus) => void;
  handleUnauthorized: () => void;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}

/**
 * Owns the access gate for every route. Children only ever render once the
 * session is known and authenticated, so individual pages never re-implement
 * the "checking access" splash or the sign-in card.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus>("checking");
  const [accessCode, setAccessCode] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  const handleUnauthorized = useCallback(() => {
    setSession({ required: true, authenticated: false });
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/session`)
      .then((response) => response.json())
      .then((data: Session) => setSession(data))
      .catch(() => setSession({ required: false, authenticated: true }));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API_URL}/health`);
        setServiceStatus(response.ok ? "online" : "offline");
      } catch {
        setServiceStatus("offline");
      }
    }, 30000);
    return () => window.clearInterval(timer);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await fetch(`${API_URL}/api/v1/session`, { method: "DELETE" });
    } finally {
      setSession({ required: true, authenticated: false });
    }
  }, []);

  async function login(event: FormEvent) {
    event.preventDefault();
    if (!accessCode.trim() || loggingIn) return;
    setLoggingIn(true);
    setLoginError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_code: accessCode.trim() }),
      });
      if (response.status === 401) {
        setLoginError("That access code was not accepted. Check it and try again.");
        return;
      }
      if (!response.ok) throw new Error("Sign-in failed");
      setAccessCode("");
      setSession({ required: true, authenticated: true });
    } catch {
      setLoginError("Sign-in is unavailable right now. Confirm the screening service is running.");
    } finally {
      setLoggingIn(false);
    }
  }

  if (session === null) {
    return (
      <main>
        <div className="auth-splash" role="status">Checking access…</div>
      </main>
    );
  }

  if (session.required && !session.authenticated) {
    return (
      <main>
        <header className="topbar">
          <div className="brand-mark">P</div>
          <div className="brand-copy">
            <h1>Parakh</h1>
            <p>Document integrity workspace</p>
          </div>
        </header>
        <section className="login-screen">
          <form className="login-card" onSubmit={login}>
            <h2>Sign in to continue</h2>
            <p>Enter the access code you were given to use the document screening workspace.</p>
            <label className="login-label" htmlFor="access-code">Access code</label>
            <input
              id="access-code"
              type="password"
              autoComplete="current-password"
              value={accessCode}
              onChange={(event) => { setAccessCode(event.target.value); setLoginError(""); }}
              autoFocus
            />
            {loginError && <div className="feedback-toast error" role="alert"><span aria-hidden="true">!</span><p><strong>Unable to sign in</strong>{loginError}</p></div>}
            <button className="primary-button" disabled={!accessCode.trim() || loggingIn}>{loggingIn ? "Signing in…" : "Sign in"}<span aria-hidden="true">→</span></button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <SessionContext.Provider
      value={{ session, serviceStatus, setServiceStatus, handleUnauthorized, signOut }}
    >
      {children}
    </SessionContext.Provider>
  );
}
