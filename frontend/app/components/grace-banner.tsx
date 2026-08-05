"use client";

import { useSession } from "../lib/session";

/**
 * Tells the user the authorization service is unreachable and how long access
 * lasts without it.
 *
 * The failure this exists to prevent is silent: the app keeps working normally
 * for days on its offline grace window and then stops, with nothing having
 * warned the person holding a deadline. The banner is quiet while there is
 * plenty of time left and turns into a caution once the backend flags `warn`,
 * so it earns attention instead of always demanding it.
 */
function formatRemaining(seconds: number): string {
  if (seconds <= 0) return "no time";
  const days = Math.floor(seconds / 86400);
  if (days >= 2) return `${days} days`;
  const hours = Math.floor(seconds / 3600);
  if (hours >= 2) return `${hours} hours`;
  const minutes = Math.max(1, Math.floor(seconds / 60));
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

export default function GraceBanner() {
  const { session } = useSession();
  const connectivity = session.connectivity;

  if (!connectivity?.offline) return null;

  const remaining = connectivity.grace_seconds_remaining;
  const urgent = connectivity.warn;
  const label = typeof remaining === "number" ? formatRemaining(remaining) : null;

  return (
    <div
      className={`grace-banner${urgent ? " urgent" : ""}`}
      role={urgent ? "alert" : "status"}
    >
      <span aria-hidden="true">{urgent ? "!" : "i"}</span>
      <p>
        <strong>
          {urgent ? "Reconnect to keep screening" : "Working offline"}
        </strong>
        {label
          ? `The authorization service is unreachable. Screening keeps working for ${label}, then new batches are blocked until this device reconnects.`
          : "The authorization service is unreachable. Reconnect this device to keep screening."}
      </p>
    </div>
  );
}
