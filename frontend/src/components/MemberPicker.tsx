// "Viewing as" member picker in the header, built on the shared Dropdown.
// Names are member-chosen text and are always rendered as plain text.
import type { RosterMember } from "../api/client";
import type { MemberId } from "../types/contracts";
import { initialOf } from "./CircleHero";
import { Dropdown } from "./Dropdown";

const ROLE_TEXT = { organiser: "Organiser", member: "Member" } as const;

export interface MemberPickerProps {
  members: RosterMember[];
  value: MemberId;
  onChange: (memberId: MemberId) => void;
}

export function MemberPicker({ members, value, onChange }: MemberPickerProps) {
  const index = Math.max(
    0,
    members.findIndex((m) => m.member_id === value),
  );
  const current = members[index];
  if (!current) return null;
  return (
    <div className="picker-wrap">
      <span className="picker__label" aria-hidden="true">
        Viewing as
      </span>
      <Dropdown
        variant="pill"
        align="right"
        items={members}
        selectedIndex={index}
        onPick={(m) => onChange(m.member_id)}
        itemKey={(m) => m.member_id}
        triggerLabel={`Viewing as ${current.display_name}, ${ROLE_TEXT[current.role]}. Change member`}
        listLabel="Choose a member"
        heading="Switch member"
        renderTrigger={(m) => (
          <>
            <span className="avatar avatar--picker" aria-hidden="true">
              {initialOf(m.display_name)}
            </span>
            <span className="picker__who">
              <bdi className="picker__name">{m.display_name}</bdi>
              <span className="picker__sub">{ROLE_TEXT[m.role]}</span>
            </span>
          </>
        )}
        renderOption={(m) => (
          <>
            <span
              className={m.role === "organiser" ? "avatar avatar--organiser" : "avatar"}
              aria-hidden="true"
            >
              {initialOf(m.display_name)}
            </span>
            <bdi className="picker__option-name">{m.display_name}</bdi>
            <span
              className={
                m.role === "organiser" ? "picker__badge picker__badge--organiser" : "picker__badge"
              }
            >
              {ROLE_TEXT[m.role]}
            </span>
          </>
        )}
      />
    </div>
  );
}
