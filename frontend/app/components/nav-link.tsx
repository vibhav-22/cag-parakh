"use client";

import { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";
import { useRouter } from "next/navigation";

type NavLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  children: ReactNode;
};

/**
 * A real anchor that routes client-side.
 *
 * `next/link` cannot be used here: under vinext it resolves its own copy of
 * React separate from the app's, so the hooks inside Link get a null dispatcher
 * and every page load throws "Cannot read properties of null (reading
 * 'useState')". This keeps the href (so copy-link, middle-click, and
 * open-in-new-tab all behave) and only hijacks an unmodified left click.
 */
export default function NavLink({ href, children, onClick, ...rest }: NavLinkProps) {
  const router = useRouter();

  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (rest.target && rest.target !== "_self") return;
    event.preventDefault();
    router.push(href);
  }

  return <a href={href} onClick={handleClick} {...rest}>{children}</a>;
}
