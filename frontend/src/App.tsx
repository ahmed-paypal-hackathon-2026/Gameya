import { useMemo, useState } from "react";
import { CIRCLE_ID, DATA_MODE, getRoster, type RosterMember } from "./api/client";
import { AsyncBlock } from "./components/AsyncBlock";
import { useAsync } from "./hooks/useAsync";
import { MemberHome } from "./pages/member/MemberHome";
import { OrganiserDashboard } from "./pages/organiser/OrganiserDashboard";
import type { MemberId, Viewer } from "./types/contracts";

function Shell({ roster }: { roster: RosterMember[] }) {
  const organiser = roster.find((m) => m.role === "organiser") ?? roster[0];
  const [viewerId, setViewerId] = useState<MemberId | undefined>(organiser?.member_id);
  const me = roster.find((m) => m.member_id === viewerId);

  const names = useMemo(() => {
    const map: Partial<Record<MemberId, string>> = {};
    for (const m of roster) map[m.member_id] = m.display_name;
    return map;
  }, [roster]);

  if (!me) return <p className="error">This circle has no members yet.</p>;
  const viewer: Viewer = { viewer_id: me.member_id, role: me.role, circle_id: CIRCLE_ID };

  return (
    <>
      <div className="viewer-bar">
        <label htmlFor="viewer">Viewing as</label>
        <select id="viewer" value={me.member_id} onChange={(e) => setViewerId(e.target.value)}>
          {roster.map((m) => (
            <option key={m.member_id} value={m.member_id}>
              {m.display_name} ({m.role})
            </option>
          ))}
        </select>
      </div>
      {/* key resets every screen's state when the viewer changes. */}
      {viewer.role === "organiser" ? (
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
    <main className="app">
      <header className="app__header">
        <h1>Gam'eya</h1>
        <span className="tag">{DATA_MODE === "mock" ? "Mock data" : "Live API"}</span>
      </header>
      <AsyncBlock state={roster} loadingText="Loading the circle...">
        {(members) => <Shell roster={members} />}
      </AsyncBlock>
    </main>
  );
}
