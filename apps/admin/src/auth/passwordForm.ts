/** Mirrors supabase/config.toml auth.minimum_password_length; Auth enforces it too. */
export const MIN_PASSWORD_LENGTH = 10;

export interface PasswordErrors {
  password?: string;
  confirm?: string;
}

export function validateNewPassword(password: string, confirm: string): PasswordErrors {
  const errors: PasswordErrors = {};
  if (!password) errors.password = "Choose a password.";
  else if (password.length < MIN_PASSWORD_LENGTH) errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  if (!errors.password && confirm !== password) errors.confirm = "The passwords don't match.";
  return errors;
}
