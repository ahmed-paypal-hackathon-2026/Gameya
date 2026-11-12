import { useEffect, useMemo, useState } from "react";
import { CIRCLE_ID, DATA_MODE, getRoster, loginAs, type RosterMember } from "./api/client";
import { AsyncBlock } from "./components/AsyncBlock";
import { initialOf } from "./components/CircleHero";
import { MemberPicker } from "./components/MemberPicker";
import { useAsync } from "./hooks/useAsync";
import { MemberHome } from "./pages/member/MemberHome";
import { OrganiserDashboard } from "./pages/organiser/OrganiserDashboard";
import type { MemberId, Viewer } from "./types/contracts";

function Shell({ roster }: { roster: RosterMember[] }) {
  const organiser = roster.find((m) => m.role === "organiser") ?? roster[0];
  const [viewerId, setViewerId] = useState<MemberId | undefined>(organiser?.member_id);
  // The persona whose token the client currently holds; screens wait for it.
  const [loggedIn, setLoggedIn] = useState<MemberId | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const me = roster.find((m) => m.member_id === viewerId);

  useEffect(() => {
    if (!me) return;
    let current = true;
    setLoggedIn(null);
    setLoginError(null);
    loginAs(me.member_id).then(
      () => current && setLoggedIn(me.member_id),
      (e: unknown) => current && setLoginError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      current = false;
    };
  }, [me]);

  const names = useMemo(() => {
    const map: Partial<Record<MemberId, string>> = {};
    for (const m of roster) map[m.member_id] = m.display_name;
    return map;
  }, [roster]);

  if (!me) return <p className="error">This circle has no members yet.</p>;
  const viewer: Viewer = { viewer_id: me.member_id, role: me.role, circle_id: CIRCLE_ID };

  return (
    <>
      <div className="persona">
        <span className="avatar avatar--lg" aria-hidden="true">
          {initialOf(me.display_name)}
        </span>
        <div className="persona__text">
          <span className="persona__hello" dir="auto">
            Hi, <bdi>{me.display_name}</bdi>
          </span>
          <span className="persona__role">{me.role === "organiser" ? "Circle organiser" : "Circle member"}</span>
        </div>
        <div className="persona__switch">
          <MemberPicker members={roster} value={me.member_id} onChange={setViewerId} />
        </div>
      </div>
      {loginError ? (
        <p className="error" role="alert">
          Could not log in: {loginError}
        </p>
      ) : loggedIn !== me.member_id ? (
        <p className="muted loading-line" role="status">
          Logging in as {me.display_name}...
        </p>
      ) : viewer.role === "organiser" ? (
        // key resets every screen's state when the viewer changes.
        <OrganiserDashboard key={viewer.viewer_id} viewer={viewer} names={names} />
      ) : (
        <MemberHome key={viewer.viewer_id} viewer={viewer} names={names} />
      )}
    </>
  );
}

export default function App() {
  const roster = useAsync(() => getRoster(CIRCLE_ID), []);
  return (
    <>
      <header className="topbar">
        <div className="topbar__inner">
          <span className="brand">
            <svg className="brand__mark" viewBox="0 0 32 32" aria-hidden="true">
              <circle cx="16" cy="16" r="11" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.35" />
              {[0, 1, 2, 3, 4, 5].map((i) => {
                const a = (i / 6) * Math.PI * 2 - Math.PI / 2;
                return (
                  <circle
                    key={i}
                    cx={16 + 11 * Math.cos(a)}
                    cy={16 + 11 * Math.sin(a)}
                    r={i === 0 ? 4 : 3}
                    className={i === 0 ? "brand__dot brand__dot--turn" : "brand__dot"}
                  />
                );
              })}
            </svg>
            <h1 className="brand__name">Gam'eya</h1>
          </span>
          <span className={DATA_MODE === "mock" ? "mode mode--mock" : "mode mode--live"}>
            {DATA_MODE === "mock" ? "Demo data" : "Live · PayPal sandbox"}
          </span>
        </div>
      </header>
      <main className="app">
        <AsyncBlock state={roster} loadingText="Loading the circle...">
          {(members) => <Shell roster={members} />}
        </AsyncBlock>
      </main>
    </>
  );
}
