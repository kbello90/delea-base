import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import {
  CATEGORIES,
  categoryLabel,
  RECEIPT_TYPES,
  useAttachReceipt,
  useCreateExpense,
  useExpense,
  useRemoveReceipt,
  useUpdateExpense,
  useViewReceipt,
  type Expense,
  type ExpenseCategory,
} from "./api";
import { DeleteExpenseDialog } from "./DeleteExpenseDialog";
import {
  changedFields,
  checkReceiptFile,
  EMPTY_FORM,
  formatDay,
  formatSize,
  toFormValues,
  validateExpenseForm,
  type ExpenseFormErrors,
  type ExpenseFormValues,
} from "./expenseForm";
import "./expenses.css";

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong. Please try again.");
const ACCEPT = RECEIPT_TYPES.map((t) => t.value).join(",");

function ExpenseForm({
  initial,
  submitLabel,
  busy,
  serverError,
  onSubmit,
  receiptPicker,
}: {
  initial: ExpenseFormValues;
  submitLabel: string;
  busy: boolean;
  serverError: string | null;
  onSubmit: (values: ExpenseFormValues, setErrors: (e: ExpenseFormErrors) => void) => void;
  receiptPicker?: React.ReactNode;
}) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<ExpenseFormErrors>({});
  const set = <K extends keyof ExpenseFormValues>(key: K, value: ExpenseFormValues[K]) => setValues((v) => ({ ...v, [key]: value }));
  const described = (key: keyof ExpenseFormValues, hint = false) =>
    errors[key] ? `expense-${key}-error` : hint ? `expense-${key}-hint` : undefined;

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(values, setErrors);
  }

  return (
    <form className="record-form" onSubmit={submit} noValidate>
      {serverError && (
        <p className="form-error" role="alert">
          {serverError}
        </p>
      )}
      <fieldset>
        <legend>Expense</legend>
        <div className="form-grid">
          <Field id="expense-date" label="Date" error={errors.date}>
            <input
              id="expense-date"
              type="date"
              value={values.date}
              onChange={(e) => set("date", e.target.value)}
              aria-invalid={Boolean(errors.date)}
              aria-describedby={described("date")}
              disabled={busy}
            />
          </Field>
          <Field id="expense-amount" label="Amount" error={errors.amount}>
            <input
              id="expense-amount"
              type="text"
              inputMode="decimal"
              value={values.amount}
              onChange={(e) => set("amount", e.target.value)}
              aria-invalid={Boolean(errors.amount)}
              aria-describedby={described("amount")}
              disabled={busy}
            />
          </Field>
        </div>
        <Field id="expense-description" label="Description" error={errors.description}>
          <input
            id="expense-description"
            type="text"
            maxLength={500}
            value={values.description}
            onChange={(e) => set("description", e.target.value)}
            aria-invalid={Boolean(errors.description)}
            aria-describedby={described("description")}
            disabled={busy}
          />
        </Field>
        <div className="form-grid">
          <Field id="expense-category" label="Category">
            <select
              id="expense-category"
              value={values.category}
              onChange={(e) => set("category", e.target.value as ExpenseCategory)}
              disabled={busy}
            >
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <label className="check">
          <input type="checkbox" checked={values.deductible} onChange={(e) => set("deductible", e.target.checked)} disabled={busy} />
          Tax-deductible
        </label>
      </fieldset>
      {receiptPicker}
      <div className="form-actions">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : submitLabel}
        </Button>
        <Link to="/expenses" className="btn-text">
          Cancel
        </Link>
      </div>
    </form>
  );
}

/** Choose a receipt file; the browser checks type and size (the server checks the content). */
function ReceiptFileInput({
  id,
  label,
  onPick,
  disabled,
}: {
  id: string;
  label: string;
  onPick: (file: File | null, error: string | null) => void;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        ref={input}
        id={id}
        type="file"
        accept={ACCEPT}
        disabled={disabled}
        aria-describedby={`${id}-hint`}
        onChange={(e) => {
          const file = e.target.files?.[0] ?? null;
          if (!file) return onPick(null, null);
          const checked = checkReceiptFile(file);
          if ("error" in checked) {
            if (input.current) input.current.value = "";
            onPick(null, checked.error);
          } else onPick(file, null);
        }}
      />
      <span id={`${id}-hint`} className="field-hint">
        A photo (JPEG, PNG or WebP) or a PDF, up to 10 MB. Location and camera details are removed from photos.
      </span>
    </div>
  );
}

function ReceiptSection({ expense, isOwner }: { expense: Expense; isOwner: boolean }) {
  const toast = useToast();
  const view = useViewReceipt();
  const attach = useAttachReceipt();
  const remove = useRemoveReceipt(expense.id);
  const [progress, setProgress] = useState<number | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [inputKey, setInputKey] = useState(0);
  const type = RECEIPT_TYPES.find((t) => t.value === expense.receipt_content_type)?.label;

  function upload(file: File | null, error: string | null) {
    setPickError(error);
    attach.reset();
    if (!file) return;
    const checked = checkReceiptFile(file);
    if ("error" in checked) return;
    setProgress(0);
    attach.mutate(
      { id: expense.id, file, type: checked.type, onProgress: setProgress },
      {
        onSuccess: () => toast({ title: expense.has_receipt ? "Receipt replaced" : "Receipt attached", variant: "success" }),
        onSettled: () => {
          setProgress(null);
          setInputKey((k) => k + 1);
        },
      },
    );
  }

  return (
    <section className="receipt-section" aria-labelledby="receipt-heading">
      <h2 id="receipt-heading">Receipt</h2>
      {expense.has_receipt ? (
        <p className="receipt-facts">
          {type} · {formatSize(expense.receipt_size_bytes ?? 0)}
          {expense.receipt_uploaded_at && <> · added {formatDay(expense.receipt_uploaded_at.slice(0, 10))}</>}{" "}
          <button type="button" className="btn-text" onClick={() => view.mutate(expense.id)} disabled={view.isPending}>
            View receipt
          </button>
        </p>
      ) : (
        <p className="muted">No receipt attached.</p>
      )}
      {view.isError && (
        <p className="form-error" role="alert">
          The receipt couldn't be opened: {message(view.error)}
        </p>
      )}
      {isOwner && (
        <>
          <ReceiptFileInput
            key={inputKey}
            id="receipt-file"
            label={expense.has_receipt ? "Replace the receipt" : "Attach a receipt"}
            onPick={upload}
            disabled={attach.isPending || remove.isPending}
          />
          {progress !== null && (
            <p className="receipt-progress">
              <progress max={100} value={Math.round(progress * 100)} aria-label="Uploading the receipt" />{" "}
              {Math.round(progress * 100)}%
            </p>
          )}
          {(pickError || attach.isError) && (
            <p className="form-error" role="alert">
              {pickError ?? message(attach.error)}
            </p>
          )}
          {expense.has_receipt && (
            <button
              type="button"
              className="btn-text btn-text--danger"
              onClick={() => {
                remove.reset();
                setConfirmRemove(true);
              }}
              disabled={attach.isPending}
            >
              Remove receipt
            </button>
          )}
          <ConfirmDialog
            open={confirmRemove}
            onOpenChange={setConfirmRemove}
            title="Remove the receipt?"
            description={<p>The receipt file is deleted. The expense stays.</p>}
            confirmLabel="Remove receipt"
            busyLabel="Removing…"
            danger
            busy={remove.isPending}
            error={remove.isError ? message(remove.error) : null}
            onConfirm={() =>
              remove.mutate(undefined, {
                onSuccess: () => {
                  setConfirmRemove(false);
                  toast({ title: "Receipt removed", variant: "success" });
                },
              })
            }
          />
        </>
      )}
    </section>
  );
}

export function NewExpensePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const create = useCreateExpense();
  const [receipt, setReceipt] = useState<File | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  const attach = useAttachReceipt();

  return (
    <>
      <PageHeader title="New expense" />
      <ExpenseForm
        initial={EMPTY_FORM}
        submitLabel="Save expense"
        busy={create.isPending || attach.isPending}
        serverError={create.isError ? message(create.error) : null}
        onSubmit={(values, setErrors) => {
          const { body, errors } = validateExpenseForm(values);
          setErrors(errors);
          if (!body) return;
          create.mutate(body, {
            onSuccess: (expense) => {
              const done = () => navigate(`/expenses/${expense.id}`);
              const checked = receipt ? checkReceiptFile(receipt) : null;
              if (!receipt || !checked || "error" in checked) {
                toast({ title: "Expense saved", variant: "success" });
                return done();
              }
              attach.mutate(
                { id: expense.id, file: receipt, type: checked.type, onProgress: () => undefined },
                {
                  onSuccess: () => toast({ title: "Expense saved with its receipt", variant: "success" }),
                  onError: (e) =>
                    toast({ title: "Expense saved, but the receipt wasn't attached", description: message(e), variant: "error" }),
                  onSettled: done,
                },
              );
            },
          });
        }}
        receiptPicker={
          <fieldset>
            <legend>Receipt (optional)</legend>
            <ReceiptFileInput
              id="new-receipt-file"
              label="Receipt file"
              onPick={(file, error) => {
                setReceipt(file);
                setPickError(error);
              }}
            />
            {pickError && (
              <p className="form-error" role="alert">
                {pickError}
              </p>
            )}
          </fieldset>
        }
      />
    </>
  );
}

function EditExpense({ expense }: { expense: Expense }) {
  const toast = useToast();
  const navigate = useNavigate();
  const update = useUpdateExpense(expense.id);
  const [deleting, setDeleting] = useState(false);

  return (
    <>
      <PageHeader
        title={expense.description}
        actions={
          <button type="button" className="btn-text btn-text--danger" onClick={() => setDeleting(true)}>
            Delete expense
          </button>
        }
      />
      <Link to="/expenses" className="btn-text back-link">
        Back to expenses
      </Link>
      <ExpenseForm
        key={expense.updated_at}
        initial={toFormValues(expense)}
        submitLabel="Save changes"
        busy={update.isPending}
        serverError={update.isError ? message(update.error) : null}
        onSubmit={(values, setErrors) => {
          const { body, errors } = validateExpenseForm(values);
          setErrors(errors);
          if (!body) return;
          const changes = changedFields(expense, body);
          if (Object.keys(changes).length === 0) return toast({ title: "Nothing to save" });
          update.mutate(changes, { onSuccess: () => toast({ title: "Expense saved", variant: "success" }) });
        }}
      />
      <ReceiptSection expense={expense} isOwner />
      <DeleteExpenseDialog expense={expense} open={deleting} onOpenChange={setDeleting} onDeleted={() => navigate("/expenses")} />
    </>
  );
}

/** The CPA's view: the record and its receipt, nothing to change. */
function ReadOnlyExpense({ expense }: { expense: Expense }) {
  const currency = useCurrency();
  return (
    <>
      <PageHeader title={expense.description} />
      <Link to="/expenses" className="btn-text back-link">
        Back to expenses
      </Link>
      <dl className="facts">
        <div>
          <dt>Date</dt>
          <dd>{formatDay(expense.expense_date)}</dd>
        </div>
        <div>
          <dt>Amount</dt>
          <dd>{currency.status === "success" ? formatMoney(expense.amount_cents, currency.currency) : "…"}</dd>
        </div>
        <div>
          <dt>Category</dt>
          <dd>{categoryLabel(expense.category)}</dd>
        </div>
        <div>
          <dt>Tax-deductible</dt>
          <dd>{expense.is_tax_deductible ? "Yes" : "No"}</dd>
        </div>
      </dl>
      <ReceiptSection expense={expense} isOwner={false} />
    </>
  );
}

export function ExpensePage() {
  const { expenseId } = useParams();
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const expense = useExpense(expenseId);

  if (expense.isPending) return <Loading label="Loading expense…" />;
  if (expense.isError) {
    const notFound = expense.error instanceof ApiError && expense.error.status === 404;
    return notFound ? (
      <>
        <PageHeader title="Expense not found" />
        <Link to="/expenses" className="btn-text">
          Back to expenses
        </Link>
      </>
    ) : (
      <ErrorState title="The expense couldn't be loaded" error={expense.error} onRetry={() => void expense.refetch()} />
    );
  }
  return isOwner ? <EditExpense expense={expense.data} /> : <ReadOnlyExpense expense={expense.data} />;
}
