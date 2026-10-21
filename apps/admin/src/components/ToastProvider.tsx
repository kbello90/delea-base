import * as Toast from "@radix-ui/react-toast";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { ToastContext, type ToastOptions } from "./toastContext";

interface Item extends ToastOptions {
  id: number;
}

/** Radix toasts: announced to screen readers, dismissible, swipe to close. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const nextId = useRef(0);

  const show = useCallback((toast: ToastOptions) => {
    nextId.current += 1;
    const id = nextId.current;
    setItems((current) => [...current, { ...toast, id }]);
  }, []);

  const remove = (id: number) => setItems((current) => current.filter((item) => item.id !== id));

  return (
    <ToastContext.Provider value={show}>
      <Toast.Provider swipeDirection="right" duration={5000}>
        {children}
        {items.map((item) => (
          <Toast.Root
            key={item.id}
            className={`toast toast--${item.variant ?? "info"}`}
            type={item.variant === "error" ? "foreground" : "background"}
            onOpenChange={(open) => !open && remove(item.id)}
          >
            <Toast.Title className="toast__title">{item.title}</Toast.Title>
            {item.description && <Toast.Description className="toast__description">{item.description}</Toast.Description>}
            <Toast.Close className="toast__close" aria-label="Dismiss">
              ×
            </Toast.Close>
          </Toast.Root>
        ))}
        <Toast.Viewport className="toast-viewport" />
      </Toast.Provider>
    </ToastContext.Provider>
  );
}
