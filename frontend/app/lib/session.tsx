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
import { usePathname } from "next/navigation";
import { API_URL } from "./format";
import { installLaunchToken, stripLaunchTokenFromUrl } from "./launch-token";
import type { ServiceStatus, Session } from "./types";

// At module scope, not in an effect: this module is imported by the root
// layout, so it evaluates before anything renders. Child effects run before
// parent effects, so installing from the provider below would let a page fire
// its first API call before the token was being attached.
installLaunchToken();

/**
 * Routes that render before anyone has signed in.
 *
 * The landing page has to be reachable by a visitor who has never seen an
 * account — showing them the sign-in card first is asking for credentials
 * before saying what the product is. Everything else stays gated.
 */
const PUBLIC_ROUTES = ["/welcome"];

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
  const pathname = usePathname();
  const isPublic = PUBLIC_ROUTES.includes(pathname);
  const [session, setSession] = useState<Session | null>(null);
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus>("checking");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  const handleUnauthorized = useCallback(() => {
    setSession({ required: true, authenticated: false });
  }, []);

  // The token is already captured by the module-scope install above; this only
  // clears it out of the address bar and the history entry.
  useEffect(() => { stripLaunchTokenFromUrl(); }, []);

  useEffect(() => {
    let active = true;

    const readSession = async (): Promise<Session> => {
      const response = await fetch(`${API_URL}/api/v1/session`);
      if (!response.ok) throw new Error("Session check failed");
      const data = await response.json() as Session;
      if (typeof data.required !== "boolean" || typeof data.authenticated !== "boolean") {
        throw new Error("Invalid session response");
      }
      return data;
    };

    readSession()
      .then((data) => { if (active) setSession(data); })
      .catch(() => { if (active) setSession({ required: true, authenticated: false }); });

    // Re-read on an interval so the grace countdown ticks down and a revoked
    // account stops being usable without waiting for the next navigation.
    // A failed poll deliberately changes nothing: the local screening service
    // being briefly busy must never look like being signed out.
    const timer = window.setInterval(() => {
      readSession()
        .then((data) => { if (active) setSession(data); })
        .catch(() => undefined);
    }, 60000);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let active = true;

    const checkService = async () => {
      try {
        const response = await fetch(`${API_URL}/health`);
        if (active) setServiceStatus(response.ok ? "online" : "offline");
      } catch {
        if (active) setServiceStatus("offline");
      }
    };

    void checkService();
    const timer = window.setInterval(checkService, 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
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
    if (!email.trim() || !password || loggingIn) return;
    setLoggingIn(true);
    setLoginError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => null) as { detail?: string } | null;
        setLoginError(error?.detail || "Sign-in was not accepted. Try again.");
        return;
      }
      const signedIn = await response.json() as Session;
      if (!signedIn.authenticated) throw new Error("Sign-in failed");
      setPassword("");
      setSession(signedIn);
    } catch {
      setLoginError("Sign-in is unavailable right now. Confirm the screening service is running.");
    } finally {
      setLoggingIn(false);
    }
  }

  // A public route renders immediately, without waiting on the session probe.
  // Blocking the landing page on a round-trip to the analysis service would
  // show a visitor "Checking access…" before they know what the product is —
  // and would blank the page entirely when that service is down.
  if (isPublic) {
    return (
      <SessionContext.Provider
        value={{
          session: session ?? { required: true, authenticated: false },
          serviceStatus,
          setServiceStatus,
          handleUnauthorized,
          signOut,
        }}
      >
        {children}
      </SessionContext.Provider>
    );
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
            {session.connectivity?.clock_tampered ? (
              // Without this the user sees a bare sign-in form and no reason
              // for it, which reads as the app randomly losing their session.
              <p>
                This device&rsquo;s clock moved backwards, so the saved session could no
                longer be trusted. Sign in once while connected to restore access.
              </p>
            ) : (
              <p>Use the approved account for this document screening workspace.</p>
            )}
            <label className="login-label" htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => { setEmail(event.target.value); setLoginError(""); }}
              autoFocus
            />
            <label className="login-label" htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => { setPassword(event.target.value); setLoginError(""); }}
            />
            {loginError && <div className="feedback-toast error" role="alert"><span aria-hidden="true">!</span><p><strong>Unable to sign in</strong>{loginError}</p></div>}
            <button className="primary-button" disabled={!email.trim() || !password || loggingIn}>{loggingIn ? "Signing in…" : "Sign in"}<span aria-hidden="true">→</span></button>
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
