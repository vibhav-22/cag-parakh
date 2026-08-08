"use client";

import { useEffect, useState } from "react";

/**
 * Loads a protected /api/ image and returns an object URL an <img> can use.
 *
 * A plain `<img src="/api/...">` is a browser resource load, not a fetch — it
 * cannot carry the x-parakh-launch header that installLaunchToken() (see
 * launch-token.ts) attaches by patching window.fetch. In an installed build
 * that header is required, so a raw <img src> to a protected endpoint 403s.
 * The endpoint cannot be exempted from that check either: these images are
 * screened document pages and evidence crops, and the whole point of the
 * launch token is that no other local process or web page should be able to
 * pull them through the loopback port.
 *
 * Routing the request through fetch (and therefore through the token patch)
 * is the fix. The resulting blob becomes an object URL, which is what gets
 * handed to the <img> tag instead of the API URL directly.
 */
export function useAuthorizedImage(url: string | null): string | null {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setObjectUrl(null);
      return;
    }
    let cancelled = false;
    let currentObjectUrl: string | null = null;

    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status}`);
        return response.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        currentObjectUrl = URL.createObjectURL(blob);
        setObjectUrl(currentObjectUrl);
      })
      .catch(() => {
        if (!cancelled) setObjectUrl(null);
      });

    return () => {
      cancelled = true;
      if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
    };
  }, [url]);

  return objectUrl;
}
