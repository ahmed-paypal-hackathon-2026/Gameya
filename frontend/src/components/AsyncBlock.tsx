import type { ReactNode } from "react";
import type { AsyncState } from "../hooks/useAsync";

export function AsyncBlock<T>({
  state,
  loadingText,
  children,
}: {
  state: AsyncState<T>;
  loadingText: string;
  children: (data: T) => ReactNode;
}) {
  if (state.status === "loading") return <p className="muted" role="status">{loadingText}</p>;
  if (state.status === "error") return <p className="error" role="alert">{state.error}</p>;
  return <>{children(state.data)}</>;
}
