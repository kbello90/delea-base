import type { ReactNode } from "react";

export interface CardProps {
  title?: string;
  children?: ReactNode;
}

export function Card({ title, children }: CardProps) {
  return (
    <section className="dl-card">
      {title ? <h2 className="dl-card__title">{title}</h2> : null}
      {children}
    </section>
  );
}
