import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icons";

export function PanelHead({ icon, titleId, title, description, aside }: {
  icon: IconName;
  titleId: string;
  title: string;
  description?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="panel-head">
      <span className="panel-icon"><Icon name={icon} /></span>
      <h2 id={titleId}>{title}</h2>
      {aside && <span className="panel-aside">{aside}</span>}
      {description && <p className="panel-description">{description}</p>}
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon: IconName; title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-icon" aria-hidden="true"><Icon name={icon} size={20} /></span>
      <p className="empty-title">{title}</p>
      {children && <p className="empty-text">{children}</p>}
    </div>
  );
}
