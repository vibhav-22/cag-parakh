"use client";

import { useState } from "react";
import {
  ArrowRight,
  CaretDown,
  Certificate,
  FileMagnifyingGlass,
  Fingerprint,
  Question,
  Stack,
} from "@phosphor-icons/react";
import NavLink from "../../components/nav-link";

/** Marketing-surface primitives for /welcome. Everything here is presentational
 *  and content-driven, so a copy change never means touching layout code. */

export type Feature = {
  icon: React.ReactNode;
  title: string;
  body: string;
};

export type Step = {
  n: string;
  title: string;
  body: string;
};

export type Faq = {
  q: string;
  a: string;
};

export function Hero() {
  return (
    <header className="welcome-hero">
      <p className="pen-eyebrow">CAG Parakh · Document integrity</p>
      <h1 className="display">
        It sees what
        <br />
        your eyes can&rsquo;t.
      </h1>
      <p className="welcome-lede">
        Parakh reads a PDF the way an examiner would — region by region — running photo,
        signature, code, typeface and tamper detectors over every page. You get a verdict,
        the region it came from, and the reason in the detector&rsquo;s own words.
      </p>
      <div className="welcome-cta">
        <NavLink className="pen-primary" href="/new">
          Start verification
          <ArrowRight weight="bold" aria-hidden="true" />
        </NavLink>
        <a className="welcome-secondary" href="#flight">
          See it read a sheet
        </a>
      </div>
      <p className="welcome-note">
        Documents are screened locally. Nothing is uploaded to a third-party service.
      </p>
    </header>
  );
}

const FEATURES: Feature[] = [
  {
    icon: <Stack aria-hidden="true" />,
    title: "Batch screening",
    body:
      "Commit a whole case at once — up to 50 documents per batch, 25 MB each — and watch the queue clear rather than opening files one at a time.",
  },
  {
    icon: <FileMagnifyingGlass aria-hidden="true" />,
    title: "Nine detectors, no more",
    body:
      "Photo, signature, QR presence, font analysis, tamper scan, moiré, same-phone, readability and metadata. That is the whole list — each owns its own pass rule.",
  },
  {
    icon: <Fingerprint aria-hidden="true" />,
    title: "Evidence, not just a score",
    body:
      "Every finding carries the region it came from and the values behind it, so a reviewer can agree or overrule it without re-reading the file.",
  },
  {
    icon: <Question aria-hidden="true" />,
    title: "It admits what it cannot do",
    body:
      "A test that could not run says why — \"only one page was analysed\" — and is never counted as a pass. Metadata is reported without a verdict at all.",
  },
  {
    icon: <Certificate aria-hidden="true" />,
    title: "Reviewable record",
    body:
      "Screening profiles are stamped onto each run and decisions are recorded against the document, so a case can be reconstructed months later.",
  },
];

export function Features() {
  return (
    <section className="welcome-features" aria-labelledby="features-heading">
      <header>
        <span className="welcome-index" aria-hidden="true">(01)</span>
        <p className="pen-eyebrow">Capabilities</p>
        <h2 id="features-heading">Built for a caseload, not a demo.</h2>
      </header>
      <ul>
        {FEATURES.map((feature) => (
          <li key={feature.title}>
            <i aria-hidden="true">{feature.icon}</i>
            <h3>{feature.title}</h3>
            <p>{feature.body}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

const STEPS: Step[] = [
  {
    n: "01",
    title: "Commit the documents",
    body: "Drop in a PDF or a whole batch. Pick the screening profile the case calls for.",
  },
  {
    n: "02",
    title: "Detectors run",
    body: "Each analyzer inspects the regions it owns and returns a status with the values behind it.",
  },
  {
    n: "03",
    title: "Review the findings",
    body: "Flagged regions surface first, with the evidence attached. You agree, overrule, or ask for more.",
  },
  {
    n: "04",
    title: "Record the decision",
    body: "The verdict, the reasoning and the profile used are stored against the document.",
  },
];

export function Workflow() {
  return (
    <section className="welcome-workflow" aria-labelledby="workflow-heading">
      <header>
        <span className="welcome-index" aria-hidden="true">(02)</span>
        <p className="pen-eyebrow">How it works</p>
        <h2 id="workflow-heading">Four steps, and the last one is yours.</h2>
      </header>
      <ol>
        {STEPS.map((step) => (
          <li key={step.n}>
            <span className="welcome-step-n" aria-hidden="true">{step.n}</span>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

const FAQS: Faq[] = [
  {
    q: "How large a file can I screen?",
    a: "Up to 25 MB per document and 50 documents in a single batch. Larger cases are better split across batches — the queue is designed to be watched, and a smaller batch clears sooner.",
  },
  {
    q: "Where do my documents go?",
    a: "They are screened locally by the analysis service and the evidence store is local. Only account and device authorization use the central service; document contents are not sent there for scoring.",
  },
  {
    q: "How fast is a screening?",
    a: "Most single documents clear in seconds; a full batch depends on page count and how many raster regions have to be extracted. Progress is reported per document rather than as one bar, so a slow file never hides the rest.",
  },
  {
    q: "How accurate is it?",
    a: "There is no single accuracy figure, and any product quoting one should be read carefully. Each detector reports its own confidence and the values it measured, and some return an explicit inconclusive with the reason it could not run. Parakh is built to inform a human decision, not to replace it.",
  },
  {
    q: "What happens when a detector cannot run?",
    a: "It says so, and why — \"only one page was analysed\", \"no raster image could be extracted\". An inconclusive result is never quietly counted as a pass.",
  },
];

function FaqItem({ item, open, onToggle }: { item: Faq; open: boolean; onToggle: () => void }) {
  return (
    <li className="welcome-faq-item" data-open={open}>
      <h3>
        <button type="button" aria-expanded={open} onClick={onToggle}>
          <span>{item.q}</span>
          <CaretDown aria-hidden="true" />
        </button>
      </h3>
      {/* Kept in the DOM and collapsed by grid rows rather than unmounted, so
          find-in-page still reaches the answers. */}
      <div className="welcome-faq-answer" role="region">
        <p>{item.a}</p>
      </div>
    </li>
  );
}

export function Faqs() {
  const [open, setOpen] = useState<string | null>(FAQS[0].q);
  return (
    <section className="welcome-faq" aria-labelledby="faq-heading">
      <header>
        <span className="welcome-index" aria-hidden="true">(03)</span>
        <p className="pen-eyebrow">Questions</p>
        <h2 id="faq-heading">Before you commit a case.</h2>
      </header>
      <ul>
        {FAQS.map((item) => (
          <FaqItem
            key={item.q}
            item={item}
            open={open === item.q}
            onToggle={() => setOpen(open === item.q ? null : item.q)}
          />
        ))}
      </ul>
    </section>
  );
}

export function SiteFooter() {
  return (
    <footer className="welcome-footer">
      <div className="welcome-footer-brand">
        <div className="brand-mark" aria-hidden="true">P</div>
        <div>
          <strong>CAG Parakh</strong>
          <p>Document integrity workspace</p>
        </div>
      </div>
      <nav aria-label="Footer">
        <div>
          <h4>Workspace</h4>
          <NavLink href="/new">New screening</NavLink>
          <NavLink href="/history">History</NavLink>
          <NavLink href="/reports">Reports</NavLink>
        </div>
        <div>
          <h4>About</h4>
          <a href="#flight">The flight</a>
          <a href="#faq-heading">Questions</a>
          <NavLink href="/settings">Settings</NavLink>
        </div>
        <div>
          <h4>Support</h4>
          <a href="mailto:support@parakh.local">support@parakh.local</a>
          <span>Access is issued per reviewer</span>
        </div>
      </nav>
      <p className="welcome-footer-legal">
        The sheet in the film is a generated specimen. Screening is advisory and the review
        stays human-led.
      </p>
    </footer>
  );
}
