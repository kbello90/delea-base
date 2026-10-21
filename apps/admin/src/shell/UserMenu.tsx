import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useAuth } from "../auth/authContext";

export function UserMenu({ email, roleLabel }: { email: string | null; roleLabel: string }) {
  const { signOut } = useAuth();
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="user-menu__trigger">
        <span className="user-menu__email">{email ?? "Account"}</span>
        <span className="user-menu__role">{roleLabel}</span>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="user-menu__content" align="end" sideOffset={6}>
          <DropdownMenu.Label className="user-menu__label">
            {email ?? "Signed in"} · {roleLabel}
          </DropdownMenu.Label>
          <DropdownMenu.Separator className="user-menu__separator" />
          <DropdownMenu.Item className="user-menu__item" onSelect={() => void signOut()}>
            Sign out
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
