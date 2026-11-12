import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { Link } from "react-router-dom";
import { api, errorText } from "../api";
import { Spinner, Wordmark } from "../components/ui";
import type { LegalDoc } from "../types";

/** /privacy and /terms: public, rendered from legal/*.md served by the backend. Raw HTML is skipped. */
export default function Legal({ name }: { name: "privacy" | "terms" }) {
  const [doc, setDoc] = useState<LegalDoc | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setDoc(null);
    setError("");
    api.legal(name).then(setDoc).catch((e) => setError(errorText(e)));
  }, [name]);

  useEffect(() => {
    document.title = doc ? `${doc.title} — Plan Your Shot` : "Plan Your Shot";
    return () => { document.title = "Plan Your Shot"; };
  }, [doc]);

  return (
    <div className="page">
      <header className="appbar">
        <Link to="/" className="appbar-brand"><Wordmark /></Link>
        <nav className="legal-nav" aria-label="Legal">
          <Link to="/terms" aria-current={name === "terms" ? "page" : undefined}>Terms</Link>
          <Link to="/privacy" aria-current={name === "privacy" ? "page" : undefined}>Privacy</Link>
        </nav>
      </header>
      <main className="legal">
        {error && <p className="inline-error" role="alert">{error}</p>}
        {!doc && !error && <Spinner />}
        {doc && (
          <article className="legal-doc">
            <Markdown skipHtml disallowedElements={["img"]}>{doc.markdown}</Markdown>
          </article>
        )}
      </main>
    </div>
  );
}
