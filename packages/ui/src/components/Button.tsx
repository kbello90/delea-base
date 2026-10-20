import type { ButtonHTMLAttributes } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost";
}

export function Button({ variant = "primary", className = "", ...rest }: ButtonProps) {
  const variantClass = variant === "ghost" ? " dl-button--ghost" : "";
  return <button className={`dl-button${variantClass} ${className}`.trim()} {...rest} />;
}
