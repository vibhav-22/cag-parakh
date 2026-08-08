"use client";

import { useAuthorizedImage } from "../lib/use-authorized-image";

/**
 * Drop-in replacement for <img> when src points at a protected /api/ endpoint.
 * See use-authorized-image.ts for why a raw <img src> cannot carry the
 * required launch-token header. A wrapper component (rather than calling the
 * hook at each call site) is what lets this be used inside .map() output,
 * where hooks cannot run directly.
 */
export default function AuthorizedImg({
  src,
  alt,
  loading,
  draggable,
  className,
}: {
  src: string;
  alt: string;
  loading?: "lazy" | "eager";
  draggable?: boolean;
  className?: string;
}) {
  const objectUrl = useAuthorizedImage(src);
  if (!objectUrl) return null;
  return <img src={objectUrl} alt={alt} loading={loading} draggable={draggable} className={className} />;
}
