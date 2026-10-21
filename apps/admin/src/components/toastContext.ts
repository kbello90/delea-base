import { createContext, useContext } from "react";

export interface ToastOptions {
  title: string;
  description?: string;
  variant?: "info" | "success" | "error";
}

export const ToastContext = createContext<((toast: ToastOptions) => void) | null>(null);

/** `const toast = useToast(); toast({ title: "Saved" })` */
export function useToast(): (toast: ToastOptions) => void {
  const show = useContext(ToastContext);
  if (!show) throw new Error("useToast must be used inside <ToastProvider>");
  return show;
}
