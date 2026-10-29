import type { ReactNode } from "react";

export type IconName =
  | "info" | "alert" | "check" | "x" | "help" | "upload" | "shield" | "play" | "back" | "arrow"
  | "film" | "lock" | "spark" | "eye" | "target" | "layers" | "chat" | "clock";

const PATHS: Record<IconName, ReactNode> = {
  info: <><circle cx="12" cy="12" r="9" /><line x1="12" y1="11" x2="12" y2="16" /><line x1="12" y1="7.8" x2="12.01" y2="7.8" /></>,
  alert: <><path d="M12 3.5 2.8 19.5h18.4z" /><line x1="12" y1="10" x2="12" y2="14" /><line x1="12" y1="16.8" x2="12.01" y2="16.8" /></>,
  check: <polyline points="5 12.5 10 17.5 19 7" />,
  x: <><line x1="6.5" y1="6.5" x2="17.5" y2="17.5" /><line x1="17.5" y1="6.5" x2="6.5" y2="17.5" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.7.3-1 .8-1 1.5v.4" /><line x1="12" y1="16.8" x2="12.01" y2="16.8" /></>,
  upload: <><path d="M4 15.5V19a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3.5" /><polyline points="8 8.5 12 4.5 16 8.5" /><line x1="12" y1="4.5" x2="12" y2="15" /></>,
  shield: <><path d="M12 3 4.5 6v5.6c0 4.4 3.1 8 7.5 9.4 4.4-1.4 7.5-5 7.5-9.4V6z" /><polyline points="8.6 12 11 14.4 15.4 9.6" /></>,
  play: <polygon points="8 5.5 18.5 12 8 18.5" fill="currentColor" stroke="none" />,
  back: <><line x1="19" y1="12" x2="5.5" y2="12" /><polyline points="11 6.5 5.5 12 11 17.5" /></>,
  arrow: <><line x1="5" y1="12" x2="18.5" y2="12" /><polyline points="13 6.5 18.5 12 13 17.5" /></>,
  film: <><rect x="3" y="5" width="18" height="14" rx="2.5" /><line x1="7.5" y1="5" x2="7.5" y2="19" /><line x1="16.5" y1="5" x2="16.5" y2="19" /><line x1="3" y1="12" x2="21" y2="12" /></>,
  lock: <><rect x="5" y="10.5" width="14" height="9.5" rx="2" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" /></>,
  spark: <path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7L4.5 10.9 10.1 9z" />,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></>,
  target: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3.2" /></>,
  layers: <><polygon points="12 3.5 21 8.2 12 12.9 3 8.2" /><polyline points="3 12.2 12 16.9 21 12.2" /><polyline points="3 16.2 12 20.9 21 16.2" /></>,
  chat: <path d="M20 14.5a2 2 0 0 1-2 2H9l-4.5 3.5V6.5a2 2 0 0 1 2-2H18a2 2 0 0 1 2 2z" />,
  clock: <><circle cx="12" cy="12" r="9" /><polyline points="12 7 12 12 15.5 14" /></>,
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="9" fill="#0f766e" />
      <path d="M6.5 24.5c3-1.2 4.6-3.6 6.2-6.1" fill="none" stroke="#5eead4" strokeWidth="2.4" strokeLinecap="round" strokeDasharray="0.1 4.2" />
      <circle cx="18" cy="13.5" r="6.6" fill="none" stroke="#ffffff" strokeWidth="2.4" />
      <circle cx="18" cy="13.5" r="2.3" fill="#ffffff" />
    </svg>
  );
}
