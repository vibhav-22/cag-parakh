/**
 * Proves to the local backend that a request came from this app window.
 *
 * An installed build runs its backend on a local port, which any other program
 * on the laptop — and any web page the user visits — can also reach. The
 * account session does not stop that: a page cannot read a cross-origin
 * response, but the request still runs, so a forged call could delete a case
 * or start work under the signed-in account. The launcher mints a token per
 * launch and gives it to both the backend and the window it opens; every API
 * request carries it back.
 *
 * This patches `fetch` once instead of threading a header through call sites.
 * There are 34 of them across 12 files and no shared wrapper, and a site
 * missed here would keep working in development and fail only in the installed
 * build — the exact failure this whole packaging effort keeps running into.
 * One interception point cannot be forgotten by code written later.
 */

const STORAGE_KEY = "parakh.launch-token";
const QUERY_PARAM = "lt";

// Lower case so it reads identically on both sides of the wire; HTTP header
// names are case-insensitive, and matching the backend spelling keeps the two
// findable together.
export const LAUNCH_TOKEN_HEADER = "x-parakh-launch";

let installed = false;
let launchToken = "";

function readStoredToken(): string {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) || "";
  } catch {
    // Storage can be unavailable (privacy modes, sandboxed frames). The
    // in-memory copy still covers this page's lifetime.
    return "";
  }
}

function storeToken(token: string): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    // Not fatal: the module-level copy survives until the window closes, and
    // the launcher passes the token again on the next launch.
  }
}

/**
 * Same-origin API calls only.
 *
 * Deliberately strict. Attaching the token to anything else would hand it to
 * whatever host the request was aimed at, and in an installed build the
 * backend is always same-origin, so nothing legitimate is excluded.
 */
function isLocalApiRequest(input: RequestInfo | URL): boolean {
  try {
    const href =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const resolved = new URL(href, window.location.href);
    return (
      resolved.origin === window.location.origin && resolved.pathname.startsWith("/api/")
    );
  } catch {
    return false;
  }
}

/**
 * Capture the token and start attaching it. Safe to call more than once, and a
 * no-op during server rendering.
 *
 * Called at module scope so the patch is in place before any component renders
 * or runs an effect. Child effects run before parent effects in React, so
 * installing this from a provider's effect would already be too late.
 */
export function installLaunchToken(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;

  const fromUrl = new URLSearchParams(window.location.search).get(QUERY_PARAM) || "";
  if (fromUrl) storeToken(fromUrl);
  launchToken = fromUrl || readStoredToken();

  // Running from source there is no launcher and no token, and the backend
  // does not ask for one. Leaving fetch untouched keeps the dev path identical
  // to what it was.
  if (!launchToken) return;

  const originalFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    if (!isLocalApiRequest(input)) return originalFetch(input, init);

    // A Request carries its own headers, so rebuild it rather than losing them
    // to `init`. Call sites all pass strings today; this keeps the patch
    // correct if one ever does not.
    if (input instanceof Request && !init) {
      const headers = new Headers(input.headers);
      headers.set(LAUNCH_TOKEN_HEADER, launchToken);
      return originalFetch(new Request(input, { headers }));
    }

    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set(LAUNCH_TOKEN_HEADER, launchToken);
    return originalFetch(input, { ...init, headers });
  };
}

/**
 * Drop the token from the address bar once it is safely in memory.
 *
 * Hygiene rather than protection — it is already stored by the time this runs.
 * Deferred to an effect so the URL never changes before hydration, which the
 * router would have to reconcile.
 */
export function stripLaunchTokenFromUrl(): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (!url.searchParams.has(QUERY_PARAM)) return;
  url.searchParams.delete(QUERY_PARAM);
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
}

/** Exposed for tests and for the launcher contract. */
export function currentLaunchToken(): string {
  return launchToken;
}
