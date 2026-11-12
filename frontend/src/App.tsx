import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, ApiError, errorText } from "./api";
import Admin from "./pages/Admin";
import Dashboard from "./pages/Dashboard";
import Legal from "./pages/Legal";
import Login from "./pages/Login";
import Workspace from "./pages/Workspace";
import type { User } from "./types";

interface Auth {
  user: User;
  logout: () => Promise<void>;
}
const AuthContext = createContext<Auth | null>(null);
export const useAuth = () => useContext(AuthContext)!;

export default function App() {
  // undefined = still checking, null = signed out
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [bootError, setBootError] = useState("");
  const location = useLocation();
  const publicPage = location.pathname === "/privacy" || location.pathname === "/terms";

  const load = useCallback(() => {
    setBootError("");
    api.me().then(setUser).catch((e) => {
      if (e instanceof ApiError && e.status === 401) setUser(null);
      else setBootError(errorText(e));
    });
  }, []);

  useEffect(load, [load]);

  // The legal pages are public: render them without waiting for (or requiring) a session.
  const legal = (
    <>
      <Route path="/privacy" element={<Legal name="privacy" />} />
      <Route path="/terms" element={<Legal name="terms" />} />
    </>
  );
  if (publicPage && !user) return <Routes>{legal}</Routes>;
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener("pys:unauthorized", onExpired);
    return () => window.removeEventListener("pys:unauthorized", onExpired);
  }, []);

  if (bootError)
    return (
      <div className="center-screen">
        <div className="notice">
          <p>{bootError}</p>
          <button className="btn" onClick={load}>Try again</button>
        </div>
      </div>
    );
  if (user === undefined) return <div className="center-screen"><span className="spinner" aria-label="Loading" /></div>;

  if (user === null)
    return (
      <Routes>
        <Route path="/login" element={<Login onSignedIn={setUser} />} />
        {legal}
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname + location.search }} />} />
      </Routes>
    );

  const logout = async () => {
    await api.logout().catch(() => undefined);
    setUser(null);
  };
  return (
    <AuthContext.Provider value={{ user, logout }}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/s/:id" element={<Workspace />} />
        <Route path="/admin" element={<Admin />} />
        {legal}
        <Route path="/login" element={<Navigate to={(location.state as { from?: string } | null)?.from || "/"} replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthContext.Provider>
  );
}
