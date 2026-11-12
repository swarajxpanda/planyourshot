import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Spinner, Wordmark } from "../components/ui";
import type { User } from "../types";

const ERRORS: Record<string, string> = {
  oauth: "Google sign-in was cancelled or failed. Please try again.",
  unverified: "Your Google account's email address isn't verified.",
};

export default function Login({ onSignedIn }: { onSignedIn: (u: User) => void }) {
  const [params] = useSearchParams();
  const [config, setConfig] = useState<{ google: boolean; dev_login: boolean } | null>(null);
  const [error, setError] = useState(ERRORS[params.get("error") ?? ""] ?? "");
  const [email, setEmail] = useState("dev@localhost");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.authConfig().then(setConfig).catch((e) => setError(errorText(e)));
  }, []);

  const devLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.devLogin(email);
      onSignedIn(await api.me());
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <section className="login-panel">
        <Wordmark />
        <h1>From screenplay<br />to shot list.</h1>
        <p className="lede">
          Upload a script, work through it with an assistant that has read every page, and leave with a shot list
          you can take to set.
        </p>
        {error && <p className="inline-error" role="alert">{error}</p>}
        {!config && !error && <Spinner />}
        {config?.google && (
          <a className="btn primary large" href="/api/auth/login">
            <span className="g-mark" aria-hidden="true">G</span>
            Continue with Google
          </a>
        )}
        {config && (
          <p className="consent">
            By continuing you confirm you are 18 or older and agree to the <Link to="/terms">Terms of Service</Link> and{" "}
            <Link to="/privacy">Privacy Policy</Link>.
          </p>
        )}
        {config && !config.google && !config.dev_login && (
          <p className="inline-error">
            Google sign-in isn't configured on this server yet. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.
          </p>
        )}
        {config?.dev_login && (
          <form className="dev-login" onSubmit={devLogin}>
            <label htmlFor="dev-email">Development sign-in</label>
            <div className="row">
              <input id="dev-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
              <button className="btn" disabled={busy}>{busy ? <Spinner small /> : "Sign in"}</button>
            </div>
            <small>Enabled by AUTH_DEV_LOGIN. Turn it off in production.</small>
          </form>
        )}
      </section>
      <aside className="login-aside" aria-hidden="true">
        <div className="slate">
          <div className="slate-row"><span>SCENE</span><span>SHOT</span><span>SIZE</span><span>LENS</span></div>
          <div className="slate-row big"><span>12</span><span>3</span><span>MCU</span><span>85mm</span></div>
          <p>Slow push-in as she reads the message. Hold on the eyes; the door stays soft in the background.</p>
        </div>
      </aside>
    </main>
  );
}
